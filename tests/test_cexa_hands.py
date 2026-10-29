"""C-EXA's hands: the exact bug Edwin hit (Excel template with a title row above the header), fixed
by C-EXA from the chat drawer, tracked as a Fix."""
import io

from openpyxl import Workbook

from tests.conftest import STRONG, login


def _excel_with_title():
    wb = Workbook()
    ws = wb.active
    ws.title = "Harvest"
    ws["A1"] = "Electricity"  # a title row — the old code took this as the header → one column
    ws.append([])
    ws.append(["Account", "Municipality", "Period", "kWh", "Demand kVA", "NMD kVA", "Excl VAT", "VAT", "Total"])
    ws.append(["5501 4471", "Ekurhuleni", "Jul 2026", 48210, 312, 350, 118402.10, 17760.32, 136162.42])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_excel_header_detection_skips_title_row():
    from cbx.pdfstudio.excel_template import read_headers

    h = read_headers(_excel_with_title())
    assert h.row_index == 3 and len([x for x in h.headers if x]) == 9
    assert h.skipped == ["Electricity"]


def test_template_from_excel_gets_all_columns(owner):
    r = owner.post("/api/pdfstudio/templates/from-excel", files={"files": ("harvest.xlsx", _excel_with_title(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200, r.text
    t = r.json()
    assert len(t["columns"]) == 9
    assert [c["ctype"] for c in t["columns"] if c["label"] == "VAT"] == ["money"]


def test_cexa_fixes_a_one_column_template_from_chat(owner, db):
    """Simulate the old bug (template saved with one column) and ask C-EXA in the drawer to fix it."""
    from cbx.models import Column, Fix, Template

    r = owner.post("/api/pdfstudio/templates/from-excel", files={"files": ("harvest.xlsx", _excel_with_title(), "application/octet-stream")})
    tid = r.json()["id"]
    # break it the way the old code did
    for c in db.query(Column).filter(Column.template_id == tid).all()[1:]:
        db.delete(c)
    db.commit()
    assert len(db.get(Template, tid).columns) == 1

    r = owner.post("/api/cexa/ask", json={"question": "i dropped a template called harvest in pdf studio but it only pulled in one column, I need all the columns", "route": "pdf-studio"})
    assert r.status_code == 200, r.text
    out = r.json()
    tools_used = [a["tool"] for a in out["actions"]]
    assert tools_used[:2] == ["inspect_template", "fix_template_columns"]
    assert "Added 8 column" in out["answer"]
    db.expire_all()
    assert len(db.get(Template, tid).columns) == 9

    fixes = owner.get("/api/cexa/fixes").json()["fixes"]
    assert fixes and fixes[0]["status"] == "fixed" and len(fixes[0]["actions"]) >= 3
    # owner feedback reopens if still broken
    r = owner.post(f"/api/cexa/fixes/{fixes[0]['id']}/feedback", json={"verdict": "still_broken", "note": "column order wrong"})
    assert r.json()["status"] == "open"
    # and the audit trail shows C-EXA acting under her own name
    acts = [a for a in owner.get("/api/audit?ai_only=true").json()["rows"] if a["action"] in ("axl_action", "pdf_template_fixed")]
    assert acts


def test_cexa_reads_code_but_never_writes_it(owner):
    r = owner.post("/api/cexa/ask", json={"question": "there is a bug, the excel import is broken", "route": "pdf-studio"})
    assert r.status_code == 200
    assert r.json()["actions"][0]["tool"] == "open_bug"
    from cbx.cexa import tools as T

    assert T.TOOLS["propose_code_change"]["tier"] == "code"
    # The only code-tier tools are the proposal itself and C-EXA's hands, which are jailed to a
    # proposal's sandbox copy (cbx/cexa/hands.py). Nothing in this set can reach the live tree.
    from cbx.cexa import hands as H

    sandbox_hands = {n for n in T.TOOLS if T.TOOLS[n]["fn"].__module__ == H.__name__}
    assert sandbox_hands >= {"write_sandbox_file", "patch_sandbox_file", "sh", "run_tests", "commit_sandbox"}
    assert all(T.TOOLS[n]["tier"] != "code" or n == "propose_code_change" or n in sandbox_hands for n in T.TOOLS)


def test_user_cannot_use_cexa_hands(user):
    assert user.post("/api/cexa/ask", json={"question": "fix my template", "route": "pdf-studio"}).status_code == 403


def test_cexa_renames_template_and_owner_can_delete(owner, db):
    from cbx.models import Template

    t = owner.post("/api/pdfstudio/templates", json={"name": "harvest 20260814 1414", "columns": [{"key": "account", "label": "Account", "ctype": "text", "required": True, "critical": False, "hint": ""}]}).json()
    r = owner.post("/api/cexa/ask", json={"question": "can you rename the harvest 1414 to Capitec, the template", "route": "pdf-studio"})
    assert r.status_code == 200, r.text
    assert [a["tool"] for a in r.json()["actions"]][0] == "rename_template"
    db.expire_all()
    assert db.get(Template, t["id"]).name == "Capitec"
    # rename via the normal UI endpoint too, then delete
    assert owner.patch(f"/api/pdfstudio/templates/{t['id']}", json={"name": "Capitec bills"}).json()["name"] == "Capitec bills"
    assert owner.delete(f"/api/pdfstudio/templates/{t['id']}").status_code == 200
    assert db.get(Template, t["id"]) is None or db.query(Template).filter(Template.id == t["id"]).count() == 0


def test_propose_refuses_imaginary_files(owner, db):
    from cbx.cexa import tools as T
    from cbx.models import User
    me = db.query(User).filter(User.role == "owner", User.is_axl.is_(False)).first()
    out = T.propose_code_change(db, me, None, title="Strip label", body="Edit cbx/startup_hooks.py line 19", files="cbx/startup_hooks.py, cbx/api/auth.py")
    assert "do not exist" in out and "cbx/startup_hooks.py" in out
    out = T.propose_code_change(db, me, None, title="Strip label", body="Edit cbx/api/auth.py", files="cbx/api/auth.py")
    assert "read these files first" in out


def test_go_ahead_in_chat_approves_pending_proposal(owner, db):
    from cbx.cexa import developer
    from cbx.models import Proposal
    from cbx.models import Fix, User
    p = developer.propose(db, "Tidy the label", "owner", "test", "cosmetic")
    me = db.query(User).filter(User.role == "owner", User.is_axl.is_(False)).first()
    db.add(Fix(title="tidy", detail="tidy", area="axl", reported_by_id=me.id, status="code_pending", kind="code", proposal_id=p.id))
    db.commit(); pid = p.id
    r = owner.post("/api/cexa/ask", json={"question": "go ahead", "route": "cexa-dev"})
    assert r.status_code == 200 and "building" in r.json()["answer"].lower()
    db.expire_all()
    assert db.get(Proposal, pid).status in ("building", "signoff", "shipped")
    r = owner.post("/api/cexa/ask", json={"question": "go ahead", "route": "cexa-dev"})
    a = r.json()["answer"].lower()
    assert "no proposal waiting" in a or "which one" in a


def test_cexa_clears_his_board_and_notices_once(owner, db):
    from cbx.cexa import developer
    from cbx.models import Proposal
    developer.propose(db, "Idea one", "requests", "group: #1, #2\nx", "x")
    developer.propose(db, "Idea two", "audit", "y", "y")
    db.commit()
    r = owner.post("/api/cexa/ask", json={"question": "remove all your notices", "route": "cexa-dev"})
    assert r.status_code == 200 and "Took" in r.json()["answer"]
    db.expire_all()
    assert not db.query(Proposal).filter(Proposal.status == "noticed").count()
    # the same request-group is never proposed twice, even under a different title
    assert developer.propose(db, "Idea one again", "requests", "group: #1, #2\nx", "x") is not None  # propose() itself is title-based …
    assert not developer._notice_requests(db)  # … but noticing checks the group key


def test_call_api_acts_as_the_person_and_blocks_human_only(owner, db):
    from cbx.cexa import tools as T
    from cbx.models import User
    me = db.query(User).filter(User.role == "owner", User.is_axl.is_(False)).first()
    out = T.call_api(db, me, None, method="POST", path="/api/develop/tasks", body='{"title": "From C-EXA", "detail": "via call_api"}')
    assert "→ 2" in out, out
    out = T.call_api(db, me, None, method="GET", path="/api/develop/tasks")
    assert "From C-EXA" in out
    out = T.call_api(db, me, None, method="POST", path="/api/users", body='{"email": "x@y.z", "name": "X", "role": "admin"}')
    assert "waits for the owner's tap" in out  # 1.0.81: rights wait for the owner (cbx/cexa/policy.py)
    out = T.list_endpoints(db, me, None, search="tasks")
    assert "/api/develop/tasks" in out


def _live_model(monkeypatch, replies):
    """Pretend Ollama is up and answers with `replies` in turn (then a clean final answer)."""
    from cbx.cexa import gateway
    from cbx.config import settings

    it = iter(replies)

    def fake(self, model, system, user, images, temperature, want_json):
        try:
            return next(it)
        except StopIteration:
            return '{"thinking": "done", "action": null, "answer": "final"}'

    monkeypatch.setattr(settings, "model_mode", "live")
    monkeypatch.setattr(gateway.Gateway, "_ollama", fake)


def test_ask_survives_a_misbehaving_live_model(owner, monkeypatch):
    """The model may answer in prose, send lists, unknown tools, wrong argument types or loop on one
    action — the drawer must always come back with 200 and something sensible, never a 500."""
    ask = lambda: owner.post("/api/cexa/ask", json={"question": "please check the excel import", "route": "pdf-studio"})  # noqa: E731
    # prose instead of JSON → the prose is the answer, not "can't reach my model"
    _live_model(monkeypatch, ["Sure — the import looks fine from here."] * 3)
    r = ask()
    assert r.status_code == 200 and r.json()["answer"].startswith("Sure")
    # unknown tool and a numeric tool name → told so, conversation continues
    _live_model(monkeypatch, ['{"thinking":"x","action":{"name":"delete_everything","args":{}},"answer":""}', '{"thinking":"x","action":{"name":12,"args":{}},"answer":""}'])
    r = ask()
    assert r.status_code == 200 and r.json()["answer"] == "final"
    assert ["delete_everything", "12"] == [a["tool"] for a in r.json()["actions"]]
    assert "don't have a tool" in r.json()["actions"][0]["result"]
    # args as a bare string, a JSON body sent as an object, answer/thinking with the wrong types
    _live_model(monkeypatch, [
        '{"thinking":"x","action":{"name":"inspect_batch","args":"999"},"answer":""}',
        '{"thinking":"x","action":{"name":"call_api","args":{"method":"GET","path":"/api/develop/tasks","body":{"a":1}}},"answer":""}',
        '{"thinking":["a","b"],"action":null,"answer":{"text":"all good"}}',
    ])
    r = ask()
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["answer"] == "all good" and out["thinking"] == "a\nb"
    assert out["actions"][0]["args"] == {"value": "999"}
    assert "→ 200" in out["actions"][1]["result"]
    # the same action over and over → stopped after the first repeat, not 8 identical calls
    _live_model(monkeypatch, ['{"thinking":"x","action":{"name":"list_templates","args":{}},"answer":""}'] * 20)
    r = ask()
    assert r.status_code == 200 and len(r.json()["actions"]) == 1
    # a list where an object was expected → graceful message
    _live_model(monkeypatch, ["[1, 2, 3]"] * 3)
    r = ask()
    assert r.status_code == 200 and "model" in r.json()["answer"].lower()


def test_gateway_turns_ollama_garbage_into_model_error(monkeypatch, db):
    import httpx

    from cbx.cexa import gateway
    from cbx.config import settings

    class R:
        def __init__(self, text, payload=None):
            self.text, self._p = text, payload

        def raise_for_status(self):
            pass

        def json(self):
            if self._p is None:
                raise ValueError("not json")
            return self._p

    monkeypatch.setattr(settings, "model_mode", "live")
    gw = gateway.Gateway(db)
    for resp in (R("<html>502 Bad Gateway</html>"), R("", {"error": "model 'x' not found"}), R("", [1, 2])):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: resp)
        try:
            gw.text("chat", "s", "u")
            assert False, "expected ModelError"
        except gateway.ModelError:
            pass
    monkeypatch.setattr(httpx, "post", lambda *a, **k: R("", {"message": {"content": "```json\n{\"a\": 1}\n```"}}))
    assert gw.json("chat", "s", "u", schema={"a": "int"}) == {"a": 1}


def test_tool_runner_never_raises_on_bad_input(owner, db):
    from cbx.cexa import tools as T
    from cbx.models import User

    me = db.query(User).filter(User.role == "owner", User.is_axl.is_(False)).first()
    assert "don't have a tool" in T.run(db, me, "nope", {}, None)
    assert "don't have a tool" in T.run(db, me, None, None, None)
    assert "Which batch" in T.run(db, me, "inspect_batch", "999", None)  # bare value → the one argument
    assert "Which batch" in T.run(db, me, "inspect_batch", ["x"], None)  # nonsense → dropped
    out = T.run(db, me, "call_api", {"method": "POST", "path": "/api/develop/tasks", "body": {"title": "Object body"}}, None)
    assert "→ 2" in out, out  # a dict body is sent as JSON, not as a Python repr
    # proposal file names survive punctuation around them
    out = T.propose_code_change(db, me, None, title="x", body="Edit `cbx/api/auth.py` (and cbx/nope.py).", files="")
    assert "cbx/nope.py" in out and "auth.py`" not in out


def test_temperatures_by_task_and_voice_keeps_facts(db, monkeypatch):
    from cbx.cexa import brain
    from cbx.cexa.gateway import Gateway, temperature_for
    from cbx.config import settings
    from cbx.models import User
    assert temperature_for("dev") == 0.0
    assert temperature_for("voice") == 2.0 and temperature_for("brief") == 2.0
    assert temperature_for("chat") == temperature_for("profile_repair") == 0.1
    me = db.query(User).filter(User.role == "owner", User.is_axl.is_(False)).first()
    gw = Gateway(db)
    monkeypatch.setattr(settings, "model_mode", "live")
    draft = "Renamed template #12 from 'harvest 1414' to 'Capitec'. It has 9 columns."
    # a hot re-voicing that keeps every fact is used …
    monkeypatch.setattr(gw, "text", lambda **k: "Done — template #12 is now 'Capitec' (was 'harvest 1414'), all 9 columns intact.")
    assert brain._voice(gw, me, "rename it", draft, []).startswith("Done")
    # … one that drops or changes a fact is thrown away in favour of the cold draft
    monkeypatch.setattr(gw, "text", lambda **k: "Done — renamed to 'Capitec', it has 8 columns now.")
    assert brain._voice(gw, me, "rename it", draft, []) == draft
    monkeypatch.setattr(gw, "text", lambda **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert brain._voice(gw, me, "rename it", draft, []) == draft


def test_model_health_tool_tells_the_owner_why_cexa_is_slow(owner, db, monkeypatch):
    """'Why are you slow?' — C-EXA reads her own model placement (GPU vs CPU), context window and 24 h latency."""
    import httpx

    from cbx.cexa import tools as T
    from cbx.config import settings
    from cbx.models import ModelCall, User

    assert T.TOOLS["model_health"]["tier"] == "read"
    me = db.query(User).filter(User.role == "owner", User.is_axl.is_(False)).first()
    out = T.run(db, me, "model_health", {}, None)
    assert out.startswith("Model: qwen3.8:27b") and "mode stub" in out and "CBX_NUM_CTX" in out
    calls = [ModelCall(task="health_test", model="qwen3.8:27b", latency_ms=95_000, tokens_in=7) for _ in range(2)]  # a task name no other test uses
    db.add_all(calls)
    db.commit()

    class R:
        def __init__(self, p):
            self.p = p

        def raise_for_status(self):
            pass

        def json(self):
            return self.p

    def get(url, timeout=None, **_):
        if url.endswith("/api/tags"):
            return R({"models": [{"name": "qwen3.8:27b"}]})
        return R({"models": [{"name": "qwen3.8:27b", "size": 20_000_000_000, "size_vram": 12_400_000_000, "context_length": 32768}]})

    monkeypatch.setattr(settings, "model_mode", "live")
    monkeypatch.setattr(httpx, "get", get)
    try:
        out = T.run(db, me, "model_health", {}, None)
        assert "Loaded: qwen3.8:27b — 62% on the GPU (11.5 of 18.6 GB), context 32768." in out
        assert "Advice: qwen3.8:27b is 62% on the GPU — 38% is running on the CPU, which is why C-EXA is slow." in out
        assert "health_test 2 calls, avg 95.0 s" in out and "Verdict: slow." in out
        # the drawer's tool runner logs it like any other action
        db.commit()  # release the write lock the tool's audit row took before the request below writes
        assert owner.get("/api/audit?ai_only=true").json()["rows"][0]["target"] == "model_health"
    finally:
        for c in calls:
            db.delete(c)
        db.commit()


def test_cexas_hands_cannot_delete_a_profile_or_do_anything_a_person_must_do(owner, db):
    """C-EXA's hands run as the person she is helping, so "is this the C-EXA account?" was never the right
    question — the session has to be marked. 1.0.81: deleting waits for the owner's tap (nothing is deleted
    meanwhile), routine work goes through, and require_human applies the same rules to her session
    (tests/test_cexa_runs_the_app.py tests the app refusing her directly)."""
    from cbx.cexa import tools as T
    from cbx.models import Profile, User

    me = db.query(User).filter(User.role == "owner", User.is_axl.is_(False)).first()
    p = Profile(key="axl.hands-off", label="Hands off", status="below_gate")
    db.add(p)
    db.commit()
    pid = p.id

    out = T.call_api(db, me, None, method="DELETE", path=f"/api/pdfstudio/profiles/{pid}")
    assert "waits for the owner's tap" in out
    out = T.call_api(db, me, None, method="POST", path="/api/pdfstudio/profiles/purge", body='{"ids": [%d]}' % pid)
    assert "waits for the owner's tap" in out
    db.commit()
    db.expire_all()
    assert db.get(Profile, pid) is not None

    # routine work reaches the route itself (a missing template: 404), not the human check
    out = T.call_api(db, me, None, method="POST", path="/api/pdfstudio/templates/999999/columns/1/reauthor")
    assert "→ 404" in out
    from cbx.models import AgentWork

    db.query(AgentWork).filter(AgentWork.kind == "api").delete()
    db.commit()
