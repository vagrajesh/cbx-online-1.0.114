"""1.0.112 (23 Oct): Edwin's dropdown again, better, for assigning people — in the Details card's "Assigned to" row, for the owner
and admins only: grouped (AXL suggests · Works <system> · Everyone else on the desk, "(admin)" labelled), a filter when the list is
long, preselected (who has it, else AXL's first, else "Choose a person…"), and only the Assign / Reassign button assigns.
Names are made up."""
import json
import re
from pathlib import Path

from ._node import run_module
from .test_support_1098 import _setup, box, lead  # noqa: F401
from .test_support_r2_screen_1109 import app_ticket

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "web/views/support.js").read_text(encoding="utf-8")


def _fn(name: str, end: str) -> str:
    i = SRC.index(name)
    return SRC[i:SRC.index(end, i)] + "}"


PRE = "const esc = (s) => String(s ?? '').replace(/[&<>\"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;', \"'\": '&#39;' }[c]));\n" \
    + "\n".join(re.findall(r"(?m)^const clipQ = .*;$", SRC)) + "\n" + _fn("function assignGroups(", "}  // assignGroups end") + "\n" \
    + _fn("function assignOptions(", "}  // assignOptions end") + "\n"
T = """const t = { system: 'Chase', assigned_to_id: null, leads: [
  { id: 1, name: 'Nadia Sims', covers: true }, { id: 2, name: 'Mira Rooi', covers: true }, { id: 3, name: 'Owen Owner', covers: false },
  { id: 4, name: 'Theo Mokoena', covers: false }, { id: 5, name: 'Bo <b>Sola</b>', covers: false }] };
const sugg = [{ id: 2, name: 'Mira Rooi', why: 'works Chase · 13 done (4 like this one) · 2 open now and a much longer tail' }];
const roles = new Map([[3, 'owner'], [4, 'manager']]);
"""


def test_the_groups_and_their_labels():
    out = json.loads(run_module(PRE + T + "console.log(JSON.stringify(assignGroups(t, sugg, roles).map((g) => [g.label, g.people.map((p) => p.text)])));"))
    assert out == [
        ["AXL suggests", ["Mira Rooi — works Chase · 13 done · 2 open now and a much…"]],
        ["Works Chase", ["Nadia Sims"]],
        ["Everyone else on the desk", ["Owen Owner (admin)", "Theo Mokoena", "Bo <b>Sola</b>"]],
    ]
    # who has it now is marked, and stays in the list even when no longer in the pool
    out = json.loads(run_module(PRE + T + "console.log(JSON.stringify([assignGroups({ ...t, assigned_to_id: 1 }, [], roles)[0].people[0].text, "
                                "assignGroups({ ...t, assigned_to_id: 9, assigned_to: 'Gone Person' }, [], roles)[0]]));"))
    assert out == ["Nadia Sims — has it", {"label": "Has it now", "people": [{"id": 9, "name": "Gone Person", "text": "Gone Person — has it"}]}]


def test_options_preselect_filter_and_escape():
    code = PRE + T + """const g = assignGroups(t, sugg, roles);
const sel = (h) => (h.match(/<option value="(\\d*)" selected>/) || [, 'none'])[1];
console.log(JSON.stringify([sel(assignOptions(g, 2, '')), sel(assignOptions(g, '', '')), sel(assignOptions(g, 2, 'theo')), sel(assignOptions(g, '', 'zzz')),
  assignOptions(g, '', 'zzz').includes('Nobody matches'), assignOptions(g, '', '').includes('Bo &lt;b&gt;Sola&lt;/b&gt;'),
  (assignOptions(g, '', '').match(/<optgroup label="([^"]+)"/g) || []).length, assignOptions(g, '', 'admin').includes('Owen Owner (admin)')]));"""
    assert json.loads(run_module(code)) == ["2", "", "4", "", True, True, 3, True]


def test_only_the_button_assigns_and_the_old_click_picker_is_gone():
    i = SRC.index("async function staffTicket(")
    body = SRC[i:SRC.index("\n}\n", i)]
    assert "sel.onchange = ready" in body and "abtn.onclick = () => { if (sel.value) assign(+sel.value, abtn); }" in body
    assert "assign(+b.dataset.sugg" not in body and "sel.value = b.dataset.sugg" in body  # a suggested name only chooses
    assert "${t.assigned_to_id ? 'Reassign' : 'Assign'}" in body and "const canPick = !!(t.can_assign && open && t.leads.length)" in body
    assert 'class="tk-row tk-row-wide"' in body  # the dropdown has the card's full width, readable when closed
    assert 'id="sp-who"' not in body and "#sp-pick" not in body and "whyNot(root.querySelector('#sp-whynot')" in body


def test_the_assign_route_and_its_why_not_are_as_before(box, owner, user):
    a = lead(owner, email="ada.a12@test.local", name="Ada Assign", systems=("chase",))
    b, bc = lead(owner, email="ben.a12@test.local", name="Ben Assign", client=True)
    tid = app_ticket(user)
    assert bc.post(f"/api/support/tickets/{tid}/assign", json={"user_id": a}).status_code == 403  # a lead can't assign
    r = owner.post(f"/api/support/tickets/{tid}/assign", json={"user_id": b})
    assert r.status_code == 200, r.text
    t = owner.get(f"/api/support/tickets/{tid}").json()
    assert t["assigned_to_id"] == b and t["can_assign"]
    assert not bc.get(f"/api/support/tickets/{tid}").json()["can_assign"]  # staff: no dropdown
