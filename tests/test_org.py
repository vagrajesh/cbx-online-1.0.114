"""The org graph must ENFORCE, not describe.

Every test here is about the gap between the picture on the canvas and what a person actually
gets back from the app. If that gap ever opens, the page is worse than useless: it tells Edwin
someone cannot see something when they can.
"""
from __future__ import annotations

import pytest

from cbx.db import SessionLocal
from cbx.models import AccessGrant, Team, User
from cbx.org import access as A


@pytest.fixture()
def chart(db, owner_pw, request):
    """ceo → two managers → two people under the first manager, plus an unrelated peer.

    Names are unique per test: the db fixture commits, so a shared slug or email would collide."""
    tag = f"{abs(hash(request.node.name)) % 100000}"
    t = Team(name=f"Org test team {tag}", slug=f"org-test-team-{tag}")
    db.add(t)
    db.flush()
    made = {}
    for key, name, role in [
        ("ceo", "Cleo Chief", "user"),
        ("mgr", "Mandla Manager", "manager"),
        ("mgr2", "Mary Manager", "manager"),
        ("lead", "Lerato Lead", "manager"),
        ("staff", "Sipho Staff", "user"),
        ("peer", "Pieter Peer", "user"),
    ]:
        u = User(email=f"{key}{tag}@org.test", name=name, role=role, team_id=t.id, password_hash="x")
        db.add(u)
        made[key] = u
    db.flush()
    A.set_reports_to(db, made["mgr"], made["ceo"].id)
    A.set_reports_to(db, made["mgr2"], made["ceo"].id)
    A.set_reports_to(db, made["lead"], made["mgr"].id)
    A.set_reports_to(db, made["staff"], made["lead"].id)
    A.set_reports_to(db, made["peer"], made["mgr2"].id)
    db.flush()
    return made


def test_the_tree_grants_ordinary_work_upward_and_never_sideways(chart, db):
    ceo, mgr, mgr2, lead, staff, peer = (chart[k] for k in ("ceo", "mgr", "mgr2", "lead", "staff", "peer"))

    # straight up the line, however many steps
    assert A.can_see(db, lead, staff.id, "folders")
    assert A.can_see(db, mgr, staff.id, "folders")
    assert A.can_see(db, ceo, staff.id, "folders")

    # never downward: your report does not see your work
    assert not A.can_see(db, staff, lead.id, "folders")
    # never sideways: a peer is not a manager, and neither is a manager of another branch
    assert not A.can_see(db, peer, staff.id, "folders")
    assert not A.can_see(db, mgr2, staff.id, "folders")
    # and always yourself
    assert A.can_see(db, staff, staff.id, "folders")


def test_a_reporting_line_is_not_consent_for_the_private_kinds(chart, db):
    """Edwin's rule: a person's To-do is their own. Being their manager is not enough."""
    lead, staff, ceo = chart["lead"], chart["staff"], chart["ceo"]
    assert A.can_see(db, lead, staff.id, "folders")
    assert not A.can_see(db, lead, staff.id, "todo")
    assert not A.can_see(db, ceo, staff.id, "todo")

    db.add(AccessGrant(owner_id=staff.id, viewer_id=lead.id, kind="todo", note="agreed in his 1:1"))
    db.flush()
    assert A.can_see(db, lead, staff.id, "todo")
    assert not A.can_see(db, ceo, staff.id, "todo")  # the wire is to the lead, not to everyone above


def test_a_wire_can_name_one_folder_instead_of_all_of_them(chart, db):
    peer, staff = chart["peer"], chart["staff"]
    db.add(AccessGrant(owner_id=staff.id, viewer_id=peer.id, kind="folders", resource_id="folder:42"))
    db.flush()
    assert A.can_see(db, peer, staff.id, "folders", "folder:42")
    assert not A.can_see(db, peer, staff.id, "folders", "folder:99")
    # and the bare question "can they see his folders at all" is still no
    assert not A.can_see(db, peer, staff.id, "folders")


def test_removing_a_wire_removes_the_access(chart, db):
    lead, staff = chart["lead"], chart["staff"]
    g = AccessGrant(owner_id=staff.id, viewer_id=lead.id, kind="todo")
    db.add(g)
    db.flush()
    assert A.can_see(db, lead, staff.id, "todo")
    db.delete(g)
    db.flush()
    assert not A.can_see(db, lead, staff.id, "todo")


def test_a_reorganisation_cannot_quietly_hand_over_private_work(chart, db):
    """Moving someone under a new manager gives that manager the ordinary kinds and nothing more."""
    mgr2, staff = chart["mgr2"], chart["staff"]
    assert not A.can_see(db, mgr2, staff.id, "folders")
    A.set_reports_to(db, staff, mgr2.id)
    assert A.can_see(db, mgr2, staff.id, "folders")
    assert not A.can_see(db, mgr2, staff.id, "todo")


def test_nobody_can_be_made_their_own_manager(chart, db):
    ceo, mgr, staff = chart["ceo"], chart["mgr"], chart["staff"]
    assert A.would_cycle(db, ceo.id, ceo.id)
    assert A.would_cycle(db, ceo.id, staff.id)  # staff is below ceo, so ceo→staff closes the loop
    with pytest.raises(ValueError, match="own manager"):
        A.set_reports_to(db, ceo, staff.id)
    with pytest.raises(ValueError):
        A.set_reports_to(db, mgr, mgr.id)
    assert ceo.reports_to_id is None  # nothing was changed by the refused move


def test_a_cycle_in_the_data_does_not_hang_the_app(chart, db):
    """set_reports_to refuses cycles, but a database edited by hand is not our promise to keep."""
    a, b = chart["mgr"], chart["mgr2"]
    a.reports_to_id = b.id
    b.reports_to_id = a.id  # straight past the guard
    db.flush()
    chain = A.managers_of(db, a.id)
    assert len(chain) < A.MAX_DEPTH and b.id in chain
    assert len(A.reports_of(db, a.id)) < 100


def test_visible_owner_ids_matches_can_see_person_by_person(chart, db):
    """The set used to filter queries and the per-person check must never disagree — that gap is
    exactly how a 'she cannot see this' on screen becomes a row in her export."""
    everyone = list(chart.values())
    for viewer in chart.values():
        for kind in ("folders", "todo", "pdf", "compliance"):
            allowed = A.visible_owner_ids(db, viewer, kind)
            assert allowed is not None  # none of these are admins
            for other in everyone:
                assert A.can_see(db, viewer, other.id, kind) == (other.id in allowed), (viewer.name, other.name, kind)


def test_admins_see_everything_and_that_is_stated_plainly(chart, db):
    boss = db.query(User).filter(User.role == "owner", User.is_axl.is_(False)).first()
    staff = chart["staff"]
    assert A.can_see(db, boss, staff.id, "todo")
    assert A.visible_owner_ids(db, boss, "todo") is None
    assert "admins see everything" in A.explain(db, boss, staff.id, "todo")


def test_explain_gives_a_reason_a_person_can_act_on(chart, db):
    lead, staff, peer, ceo = chart["lead"], chart["staff"], chart["peer"], chart["ceo"]
    assert "report" in A.explain(db, lead, staff.id, "folders").lower()
    assert "steps up" in A.explain(db, ceo, staff.id, "folders")
    assert "private unless" in A.explain(db, lead, staff.id, "todo")
    assert A.explain(db, peer, staff.id, "folders").startswith("No.")
    db.add(AccessGrant(owner_id=staff.id, viewer_id=peer.id, kind="folders", note="covering while he is on leave"))
    db.flush()
    out = A.explain(db, peer, staff.id, "folders")
    assert "wire was drawn" in out and "covering while he is on leave" in out


def test_an_unknown_kind_is_refused_not_quietly_allowed(chart, db):
    with pytest.raises(ValueError):
        A.can_see(db, chart["peer"], chart["staff"].id, "salary")
    with pytest.raises(ValueError):
        A.visible_owner_ids(db, chart["peer"], "salary")


def test_a_wire_to_one_folder_does_not_widen_the_query_filter(chart, db):
    """The bug this test exists for: `visible_owner_ids` once counted a one-folder wire as access to
    the owner's whole kind. A screen filtering by owner id would then have shown every folder Sipho
    has because Pieter was given one — the exact drift this module promises cannot happen."""
    peer, staff = chart["peer"], chart["staff"]
    db.add(AccessGrant(owner_id=staff.id, viewer_id=peer.id, kind="folders", resource_id="folder:42"))
    db.flush()
    assert staff.id not in A.visible_owner_ids(db, peer, "folders")
    assert A.resource_grants(db, peer, "folders") == {(staff.id, "folder:42")}
    # and a whole-kind wire still does widen it
    db.add(AccessGrant(owner_id=staff.id, viewer_id=peer.id, kind="pdf"))
    db.flush()
    assert staff.id in A.visible_owner_ids(db, peer, "pdf")
    assert A.resource_grants(db, peer, "pdf") == set()


def test_explain_never_says_yes_where_can_see_says_no(chart, db):
    """A narrow wire is not an answer to the broad question. If explain says 'a wire was drawn' while
    can_see says no, the canvas shows a contradiction and nobody trusts the page again."""
    peer, staff = chart["peer"], chart["staff"]
    db.add(AccessGrant(owner_id=staff.id, viewer_id=peer.id, kind="folders", resource_id="folder:42"))
    db.flush()
    why = A.explain(db, peer, staff.id, "folders")
    assert not A.can_see(db, peer, staff.id, "folders")
    assert "folder:42" in why and "not for the rest" in why

    # and every viewer/kind pair must agree, narrow wires included
    for viewer in chart.values():
        for kind in ("folders", "todo", "pdf", "compliance"):
            for other in chart.values():
                allowed = A.can_see(db, viewer, other.id, kind)
                said_yes = A.explain(db, viewer, other.id, kind).startswith(("No.", "Only "))
                assert allowed != said_yes or viewer.id == other.id, (viewer.name, other.name, kind)
