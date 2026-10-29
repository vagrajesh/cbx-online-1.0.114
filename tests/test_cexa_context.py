"""C-EXA has to know the words her own app uses.

On 17 Sep a controller filed "Please add Nigeria Exxon mobile. it's a new entity" and C-EXA replied
asking whether an entity was "a client, a product, a phone number, a division". Entity is a table
in this app and Mozambique is one of its rows. These tests are about that never happening again.
"""
from __future__ import annotations

from cbx.cexa import context as AC
from cbx.models import Team
from cbx.models.docs import DocType, Entity


def test_the_vocabulary_names_the_real_rows_not_just_the_concepts(owner, db):
    db.add(Entity(name="Mozambique"))
    db.add(Entity(name="A&T Morocco"))
    db.add(DocType(name="NUIT", prefix="NUIT", order=500))
    db.add(Team(name="Controllership", slug="controllership-ctx"))
    db.commit()

    text = AC.for_request(db)
    # the concept, so an unseen name still lands in the right place
    assert "ENTITY" in text and "Entity Compliance grid" in text
    # and the actual values, so a name in a request resolves instead of being questioned
    for name in ("Mozambique", "A&T Morocco", "NUIT", "Controllership"):
        assert name in text, name


def test_it_says_plainly_not_to_interrogate_a_clear_request(owner, db):
    text = AC.for_request(db)
    assert "do not ask them what it is" in text
    assert "One question, not four" in text
    # the two words that actually tripped him up are named
    assert "NUIT is Mozambique's tax" in text
    assert "it's a new entity" in text or "new entity" in text


def test_the_block_survives_an_empty_database_and_stays_bounded(owner, db):
    """It is built fresh on every request, so it must be cheap and must never raise."""
    text = AC.vocabulary(db)
    assert "none yet" in text or "Entities on the grid" in text
    for i in range(AC.MAX_NAMES + 25):
        db.add(Entity(name=f"Bulk Entity {i:03d}"))
    db.commit()
    text = AC.vocabulary(db)
    assert "more)" in text  # capped, and honest that it capped
    assert len(text) < 8000


def test_the_assessor_is_given_the_vocabulary(owner, db, monkeypatch):
    """Not just that the module exists — that the prompt actually carries it."""
    from cbx import requests_flow as RF
    from cbx.models import Request

    db.add(Entity(name="Nigeria"))
    db.commit()
    seen = {}

    class FakeGateway:
        def __init__(self, _db):
            pass

        def json(self, *, task, system, user, schema):
            seen["user"] = user
            return {"importance": "low", "confidence": 0.9, "title": "Add an entity"}

    monkeypatch.setattr(RF, "Gateway", FakeGateway)
    me = db.query(__import__("cbx.models", fromlist=["User"]).User).filter_by(role="owner", is_axl=False).first()
    req = Request(number=9901, user_id=me.id, problem="Please add Nigeria Exxon mobile. it's a new entity", impact="", wish="")
    db.add(req)
    db.flush()
    RF.assess(db, req)
    assert "WHAT CBX HOLDS RIGHT NOW" in seen["user"]
    assert "Nigeria" in seen["user"]
    assert req.confidence >= 0.9  # a small, reversible job should not score as a mystery
