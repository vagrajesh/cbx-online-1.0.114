"""1.0.101: from CBX-Connect 2.10.6 tests/test_ap_2106.py, on the old conftest (the repair is a once-routine; the GPU gate
is the old gateway's, where C-EXA's council call team_lens held the one slot).
1.0.101: the utilities list is built in (the Harvester's vendor_mappings.xlsx), what waits for it in Other goes back to
Mia when it changes (and once at the first start), Other's choices fit the reason (Add to the utilities list, never "New
profile" for a bill PDF Studio reads), and the capture readers go before C-EXA's background team work on the GPU."""
from __future__ import annotations

import csv
import threading
import time
import uuid

from cbx.ap import capture as C
from cbx.ap import masters as M
from cbx.cexa import gateway as G
from cbx.config import settings
from cbx.db import SessionLocal
from cbx.models import AppState, AuditLog
from cbx.models.ap import ApCapture, ApItem, ApUtilityAccount

from .test_ap_capture_1098 import _admin, _app, _drop, _user, _xlsx, eskom_pdf  # noqa: F401

BELA = "0001009715"
REFUSED = "PDF Studio already reads these bills (profile Eskom). Add the account to the utilities list instead."


def _seed_rows(account_key: str) -> list[dict]:
    return [r for r in M.seed_utilities() if r["account_key"] == account_key]


def _drop_account(account_key: str) -> None:
    with SessionLocal() as s:
        s.query(ApUtilityAccount).filter(ApUtilityAccount.account_key == account_key).delete()
        s.commit()


def _cap(cid: int) -> ApCapture:
    with SessionLocal() as s:
        c = s.get(ApCapture, cid)
        s.expunge(c)
        return c


def _hist(item_id: int) -> list[str]:
    with SessionLocal() as s:
        return [h["text"] for h in s.get(ApItem, item_id).history or []]


def _other(u, bid: int) -> list[dict]:
    return [x for x in u.get("/api/ap/needs").json()["other"] if x["batch_id"] == bid]


# ---- 1. the utilities list is built in ------------------------------------------------------------------------------------
def test_the_seed_carries_the_harvesters_list_with_its_leading_zeros():
    with M.UTILITIES_CSV.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0][:6] == ["VENDOR/BP NUMBER", "PID/CC", "NAME1", "NAME3", "ACCOUNT REFERENCE", "MUNICIPALITY/PAYEE"] and len(rows) - 1 == 10631
    assert ["709642", "07267", "Bela Bela ETE", "6000/07267/X1", BELA, "BELA-BELA LOCAL MUNICIPALITY"] in [r[:6] for r in rows]
    seeded = M.seed_utilities()
    assert len(seeded) == 10345  # the 286 rows with no account number ("TELKOM", "NEW CONNECTION", "0" …) are left out, as an upload does
    with SessionLocal() as s:  # into an empty table, once, the way the other SAP lists are seeded
        s.query(ApUtilityAccount).delete()
        s.query(AppState).filter(AppState.key == "ap.seeded.utilities").delete()
        s.commit()
        t0 = time.monotonic()
        M.seed(s)
        s.commit()
        assert time.monotonic() - t0 < 20
        assert s.query(ApUtilityAccount).count() == 10345 and s.query(ApUtilityAccount).filter(ApUtilityAccount.source != "seed").count() == 0
        a = s.query(ApUtilityAccount).filter(ApUtilityAccount.account_key == "1009715").one()
        assert (a.account, a.vendor, a.cost_centre, a.site, a.name3, a.municipality) == (BELA, "709642", "07267", "Bela Bela ETE", "6000/07267/X1", "BELA-BELA LOCAL MUNICIPALITY")
        assert a.extra.get("District") == "North Eastern" and a.extra.get("Region") == "NW"  # the master list's own columns, beside the row
        s.query(ApUtilityAccount).filter(ApUtilityAccount.id == a.id).delete()
        s.commit()
        M.seed(s)  # once: a row an admin removed is not put back by the next start
        s.commit()
        assert s.query(ApUtilityAccount).count() == 10344
        M._insert_utilities(s, _seed_rows("1009715"))
        s.commit()
    # the page finds it as an upload would show it
    row = _admin().get(f"/api/ap/masters/utilities?q={BELA}").json()["rows"][0]
    assert (row["vendor"], row["cost_centre"], row["site"], row["source"]) == ("709642", "07267", "Bela Bela ETE", "seed")


# ---- 2. what waits in Other goes back to Mia --------------------------------------------------------------------------------
def test_the_repair_sends_waiting_bills_back_to_mia_including_new_profile_ones():
    run = uuid.uuid4().hex[:6].upper()
    u = _user()
    _drop_account("1009715")  # as on Edwin's copy: the list did not have it
    b = _drop(u, {f"BEL_1009715_Statement ({i}) {run}.pdf": eskom_pdf(account=BELA, invoice=f"71{i}{run[:3]}") for i in range(3)}, f"Bela {run}")
    other = _other(u, b["id"])
    assert len(other) == 3 and all(x["needs"] == "entity" and x["profile_known"] == "Eskom" for x in other)
    with SessionLocal() as s:  # two of them were sent to "New profile" before 1.0.101 (the profile exists)
        for x in other[:2]:
            s.get(ApCapture, x["id"]).needs = "profile"
        s.commit()
    with SessionLocal() as s:  # the list is loaded (the seed)
        M._insert_utilities(s, _seed_rows("1009715"))
        s.commit()
    assert C.run_rematch(force=True) >= 3
    for x in other:
        c = _cap(x["id"])
        assert (c.bucket, c.needs, c.vendor_no) == ("utility", "", "709642") and c.stage in ("test", "ready"), (c.bucket, c.stage, c.needs)
        assert c.match["account"]["cc"] == "07267" and c.match["account"]["name1"] == "Bela Bela ETE"
        assert C.REMATCH_LINE in _hist(x["item_id"])
    with SessionLocal() as s:
        audit = s.query(AuditLog).filter(AuditLog.action == "ap_rematched").order_by(AuditLog.id.desc()).first()
        assert audit is not None and audit.meta.get("count", 0) >= 3 and audit.detail.startswith(f"{audit.meta['count']} back to Mia") and audit.actor_label == "system"
        assert s.get(AppState, C.REPAIR_REMATCH) is not None
    # once: a second start does nothing
    assert C.run_rematch() is None


def test_a_new_profile_bill_whose_account_is_still_missing_comes_back_with_the_right_choices():
    run = uuid.uuid4().hex[:6].upper()
    acct = "60" + str(uuid.uuid4().int)[:8]
    u = _user()
    b = _drop(u, {f"np_{run}.pdf": eskom_pdf(account=acct, invoice=f"72{run[:4]}")}, f"NP {run}")
    x = _other(u, b["id"])[0]
    with SessionLocal() as s:
        s.get(ApCapture, x["id"]).needs = "profile"
        s.commit()
    with SessionLocal() as s:
        C.rematch(s)  # nothing changed for its account: it waits
        s.commit()
    assert _cap(x["id"]).needs == "profile"
    with SessionLocal() as s:
        C.rematch(s, repair=True)
        s.commit()
    c = _cap(x["id"])
    assert (c.bucket, c.needs) == ("other", "entity")  # Mia put it back in Other, now with Add to the utilities list
    assert C.KNOWN_LINE.format(name="Eskom") in _hist(x["item_id"])


def test_an_upload_of_the_list_rematches_what_waits_for_it():
    run = uuid.uuid4().hex[:6].upper()
    acct = "61" + str(uuid.uuid4().int)[:8]
    u, adm = _user(), _admin()
    b = _drop(u, {f"up_{run}.pdf": eskom_pdf(account=acct, invoice=f"73{run[:4]}")}, f"Up {run}")
    x = _other(u, b["id"])[0]
    sheet = _xlsx([["VENDOR/BP NUMBER", "PID/CC", "NAME1", "NAME3", "ACCOUNT REFERENCE", "MUNICIPALITY/PAYEE"],
                   [709642, "07267", "Bela Bela ETE", "6000/07267/X1", BELA, "BELA-BELA LOCAL MUNICIPALITY"],
                   [705555, "00412", "Upload Site", "6000/00412/X1", "00" + acct, "ESKOM"]])
    r = adm.post("/api/ap/masters/utilities/import", files={"file": ("vendor_mappings.xlsx", sheet)}, data={"confirm": "false"})
    assert r.status_code == 200 and "rematched" not in r.json() and _cap(x["id"]).bucket == "other"  # a preview moves nothing
    r = adm.post("/api/ap/masters/utilities/import", files={"file": ("vendor_mappings.xlsx", sheet)}, data={"confirm": "true"})
    assert r.status_code == 200 and r.json()["saved"] and r.json()["rematched"] >= 1
    c = _cap(x["id"])
    assert (c.bucket, c.vendor_no) == ("utility", "705555") and c.match["account"]["cc"] == "00412"
    assert C.REMATCH_LINE in _hist(x["item_id"])
    with SessionLocal() as s:  # the seed again for the tests after this one
        s.query(ApUtilityAccount).delete()
        M._insert_utilities(s, M.seed_utilities())
        s.commit()


# ---- 3. Other's choices fit the reason ----------------------------------------------------------------------------------------
def test_other_offers_add_to_the_list_and_refuses_new_profile_for_a_known_profile():
    run = uuid.uuid4().hex[:6].upper()
    acct = "62" + str(uuid.uuid4().int)[:8]
    u, adm = _user(), _admin()
    files = {f"a{i}_{run}.pdf": eskom_pdf(account=acct, invoice=f"74{i}{run[:3]}") for i in range(2)}
    b = _drop(u, files, f"Add {run}")
    other = _other(adm, b["id"])
    assert len(other) == 2
    x = other[0]
    assert x["profile_known"] == "Eskom" and x["add"]["account"] == acct and x["add"]["municipality"]  # the form's prefill
    # New profile is refused, in plain words; the page never offers it for these
    r = u.post(f"/api/ap/captures/{x['id']}/decide", json={"action": "profile"})
    assert r.status_code == 400 and r.json()["detail"] == REFUSED
    js = (M.K.DATA.parent.parent / "web" / "views" / "ap.js").read_text(encoding="utf-8")
    assert 't("Add to the utilities list")' in js and "c.profile_known" in js
    # admins only add accounts
    body = {"action": "add_account", "data": {"vendor": "706666", "cost_centre": "00777", "site": f"Site {run}"}}
    assert u.post(f"/api/ap/captures/{x['id']}/decide", json=body).status_code == 403
    assert u.post(f"/api/ap/captures/{x['id']}/decide", json={"action": "manual", "data": {"vendor": "706666"}}).status_code == 403
    r = adm.post(f"/api/ap/captures/{x['id']}/decide", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["bucket"] == "utility" and r.json()["vendor_no"] == "706666"
    with SessionLocal() as s:
        a = s.query(ApUtilityAccount).filter(ApUtilityAccount.account_key == acct).one()
        assert (a.vendor, a.cost_centre, a.site, a.municipality) == ("706666", "00777", f"Site {run}", x["add"]["municipality"]) and a.source == "person"  # 1.0.104: an admin's own addition waits for a second admin too
        assert s.query(AuditLog).filter(AuditLog.action == "ap_utility_account", AuditLog.target == f"ap_account:{acct}").count() == 1
        assert s.query(AuditLog).filter(AuditLog.action == "ap_capture_account").count() >= 1
    # the other bill of the same account went back to Mia by itself
    c2 = _cap(other[1]["id"])
    assert (c2.bucket, c2.vendor_no) == ("utility", "706666") and C.REMATCH_LINE in _hist(other[1]["item_id"])
    # a manual invoice in Other (no profile knows it) keeps New profile
    assert all("profile_known" not in y for y in adm.get("/api/ap/needs").json()["other"] if (y.get("fields") or {}).get("kind") == "manual")


# ---- 4. the readers go before C-EXA's background team work --------------------------------------------------------------------------
def test_the_gpu_gate_lets_a_capture_reader_go_before_a_queued_council_call(monkeypatch):
    monkeypatch.setattr(settings, "gpu_slots", 1)
    gate, order = G.GpuGate(), []
    assert {"ap_capture", "ap_capture_senior", "ap_match", "support_read", "support_see"} <= G.FOREGROUND and "team_lens" not in G.FOREGROUND

    def call(task):
        with gate.use(task):
            order.append(task)

    with gate.use("busy"):
        bg = threading.Thread(target=call, args=("team_lens",))  # C-EXA's council, queued first
        bg.start()
        time.sleep(0.3)
        rd = threading.Thread(target=call, args=("ap_capture",))
        rd.start()
        time.sleep(0.3)
    bg.join(10)
    rd.join(10)
    assert order == ["ap_capture", "team_lens"]
