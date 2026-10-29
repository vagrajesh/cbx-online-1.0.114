"""Site Schedules: the Swiftnet quote parser, and the one-time copy of the old page's quotations into the tool
folder (tools/site-schedules; the old page and its API went in 1.0.79)."""
from tests._pdfs import swiftnet_quote

from cbx.models import ScheduleJob, User
from cbx.schedules import parser
from cbx.warehouse import storage
from cbx.warehouse.extract import pdf_text


def _old_quotation(db, data, name="Swiftnet_Q-77001.pdf"):
    """A quotation as the old Site Schedules page kept it (before 1.0.56)."""
    me = db.query(User).filter(User.email == "admin@test.local").one()
    text, pages = pdf_text(data)
    f = storage.store(db, me, name, data, sub="Inbox", text=text, pages=pages, meta={"purpose": "site-schedule"})
    p = parser.parse(text)
    j = ScheduleJob(user_id=me.id, quote_file_id=f.id, site=p["site"], quote_no=p["quote_no"], fields=p["fields"], issues=p["issues"], status="needs_input" if p["issues"] else "parsed")
    db.add(j)
    db.flush()
    return j


def test_money_parsing():
    from cbx.schedules.parser import parse_money

    assert parse_money("R 1 250,00") == {"value": 1250.0, "ambiguous": False, "display": "R 1 250.00"}
    amb = parse_money("R1,250,00")
    assert amb["value"] == 1250.0 and amb["ambiguous"] is True
    assert parse_money("R1,250.00")["value"] == 1250.0
    assert parse_money("R 18 500,00")["value"] == 18500.0
    assert parse_money("R 0,00")["value"] == 0.0


def test_a_quote_is_read_and_what_is_unclear_is_listed():
    p = parser.parse(pdf_text(swiftnet_quote())[0])
    assert p["site"] == "Upington" and p["quote_no"] == "Q-88240" and p["issues"]
    clean = parser.parse(pdf_text(swiftnet_quote(electricity="R 1 250,00 per month", site="Tiervlei", quote_no="Q-88213"))[0])
    assert clean["issues"] == [] and clean["fields"]["electricity"]["value"] == 1250.0


def test_old_quotations_are_copied_into_the_tool_once(admin, db, tmp_path, monkeypatch):
    """1.0.56: the built-in page's quotations (and their files) are copied into tools/site-schedules once."""
    from cbx import state
    from cbx.config import ROOT
    from cbx.engine import files as FILES
    from cbx.engine import loader
    from cbx.engine import store as STORE
    from cbx.models import AppState
    from cbx.schedules import copy_in

    loader.reload(ROOT / "tools")
    _old_quotation(db, swiftnet_quote(site="Kimberley", quote_no="Q-77001"))
    monkeypatch.setattr(STORE, "path_for", lambda slug: tmp_path / f"{slug}.sqlite")
    monkeypatch.setattr(FILES, "root_for", lambda slug: tmp_path / "_files" / slug)
    db.query(AppState).filter(AppState.key == copy_in.KEY).delete()
    got = copy_in.run_at_start(db)
    assert got["quotations"] >= 1 and got["files"] >= 1
    t = loader.registry().tools["site-schedules"]
    rows = STORE.Store("site-schedules").rows("quotes", t.data["quotes"]["columns"], limit=2000)["rows"]
    r = next(x for x in rows if x["col_id"] == "Q-77001")  # 1.0.88: the table has CBX-Connect 2's fields
    assert r["site_name"] == "Kimberley" and r["status"] == "Read" and r["quote"]["name"].endswith(".pdf")
    assert any("comma" in c["text"] and not c["done"] for c in r["checks"])  # what was unclear is still to check
    assert r["created_by"]  # who added it is kept
    assert copy_in.run_at_start(db) == {} and state.get(db, copy_in.KEY)
    db.rollback()
