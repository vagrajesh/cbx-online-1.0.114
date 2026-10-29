def test_login_and_roles(owner, admin, user):
    assert owner.get("/api/auth/me").json()["user"]["role"] == "owner"
    assert admin.get("/api/auth/me").json()["user"]["role"] == "admin"
    assert user.get("/api/auth/me").json()["user"]["role"] == "user"


def test_admin_cannot_create_admin(admin):
    r = admin.post("/api/users", json={"email": "x@test.local", "name": "X", "role": "admin"})
    assert r.status_code == 403
    r = admin.post("/api/users", json={"email": "y@test.local", "name": "Y", "role": "user"})
    assert r.status_code == 200


def test_user_cannot_see_admin_pages(user):
    assert user.get("/api/users").status_code == 403
    assert user.get("/api/audit").status_code == 403


def test_lockout(owner):
    pw = owner.post("/api/users", json={"email": "lock@test.local", "name": "Lock", "role": "user"}).json()["temp_password"]
    from fastapi.testclient import TestClient

    from cbx.main import app

    c = TestClient(app)
    for _ in range(5):
        assert c.post("/api/auth/login", json={"email": "lock@test.local", "password": "wrong-wrong-wrong"}).status_code == 401
    # locked — and the message says how long, not "ask the owner" (a wrong-password lock expires)
    r = c.post("/api/auth/login", json={"email": "lock@test.local", "password": pw})
    assert r.status_code == 401 and "try again in" in r.json()["detail"].lower()


def test_audit_records_actions(owner):
    rows = owner.get("/api/audit").json()["rows"]
    actions = {r["action"] for r in rows}
    assert {"login", "account_created"} & actions


def test_weak_password_rejected(owner):
    pw = owner.post("/api/users", json={"email": "weak@test.local", "name": "Weak", "role": "user"}).json()["temp_password"]
    from fastapi.testclient import TestClient

    from cbx.main import app

    c = TestClient(app)
    c.post("/api/auth/login", json={"email": "weak@test.local", "password": pw})
    r = c.post("/api/auth/change-password", json={"new_password": "short", "repeat": "short"})
    assert r.status_code == 400


def _fresh_client():
    from fastapi.testclient import TestClient

    from cbx.main import app

    return TestClient(app)


def test_password_change_signs_out_other_sessions(owner):
    from tests.conftest import STRONG, _make, login

    pw = _make(owner, "revoke@test.local", "Revoke Me", "user")
    a = _fresh_client()
    login(a, "revoke@test.local", pw)  # sets STRONG on first sign-in
    b = _fresh_client()
    login(b, "revoke@test.local", STRONG)
    assert b.get("/api/auth/me").status_code == 200
    new = "a-brand-new-passphrase-42"
    r = a.post("/api/auth/change-password", json={"current_password": STRONG, "new_password": new, "repeat": new})
    assert r.status_code == 200, r.text
    assert a.get("/api/auth/me").status_code == 200  # the session that changed it stays
    assert b.get("/api/auth/me").status_code == 401  # every other one is out
    # re-using the current password is refused
    r = a.post("/api/auth/change-password", json={"current_password": new, "new_password": new, "repeat": new})
    assert r.status_code == 400


def test_disable_account_signs_out_and_self_disable_is_refused(owner):
    from tests.conftest import _make, _uid, login

    pw = _make(owner, "disable@test.local", "Dis Able", "user")
    c = _fresh_client()
    login(c, "disable@test.local", pw)
    uid = _uid(owner, "disable@test.local")
    assert owner.patch(f"/api/users/{uid}", json={"is_active": False}).status_code == 200
    assert c.get("/api/auth/me").status_code == 401
    assert owner.patch(f"/api/users/{uid}", json={"is_active": True}).status_code == 200
    assert c.get("/api/auth/me").status_code == 401  # re-enabling doesn't resurrect old sessions
    me = owner.get("/api/auth/me").json()["user"]["id"]
    assert owner.patch(f"/api/users/{me}", json={"is_active": False}).status_code == 400
    assert owner.get("/api/auth/me").status_code == 200


def test_admin_cannot_touch_admins_or_owners(owner, admin):
    from tests.conftest import _uid

    owner_id = owner.get("/api/auth/me").json()["user"]["id"]
    admin_id = _uid(owner, "admin@test.local")
    assert admin.patch(f"/api/users/{owner_id}", json={"name": "Hacked"}).status_code == 403
    assert admin.patch(f"/api/users/{admin_id}", json={"role": "owner"}).status_code == 403
    assert admin.post(f"/api/users/{owner_id}/temp-password").status_code == 403
    # admin can't promote a user either
    uid = admin.post("/api/users", json={"email": "promote-me@test.local", "name": "P", "role": "user"}).json()["user"]["id"]
    assert admin.patch(f"/api/users/{uid}", json={"role": "admin"}).status_code == 403
    assert owner.get("/api/users").json()["users"]
    blocked = [r for r in owner.get("/api/audit?action=role_grant_blocked").json()["rows"]]
    assert blocked and blocked[0]["outcome"] == "blocked"


def test_bad_references_are_400_not_500(owner):
    from tests.conftest import _uid

    r = owner.post("/api/users", json={"email": "badteam@test.local", "name": "B", "role": "user", "team_id": 999999})
    assert r.status_code == 400
    r = owner.post("/api/users", json={"email": "badtool@test.local", "name": "B", "role": "user", "tools": [999999]})
    assert r.status_code == 400
    r = owner.post("/api/users/rules", json={"table": "master_rows", "field": "client", "value": "Absa", "user_id": 999999})
    assert r.status_code == 400
    r = owner.post("/api/users/rules", json={"table": "master_rows", "field": "client", "value": "   ", "user_id": _uid(owner, "owner@test.local")})
    assert r.status_code == 400
    tools = owner.get("/api/users").json()["tools"]
    admin_only = next((t for t in tools if t["admin_only"]), None)
    uid = owner.post("/api/users", json={"email": "plain@test.local", "name": "Plain", "role": "user"}).json()["user"]["id"]
    if admin_only:
        assert owner.post("/api/users/access", json={"user_id": uid, "tool_id": admin_only["id"], "granted": True}).status_code == 400


def test_settings_prefs_only_keep_simple_values(user):
    r = user.patch("/api/settings", json={"prefs": {"compact": True, "evil": {"nested": [1, 2]}, "note": "x" * 500}})
    assert r.status_code == 200
    prefs = r.json()["prefs"]
    assert prefs["compact"] is True and "evil" not in prefs and "note" not in prefs
    r = user.patch("/api/settings", json={"name": "  Thandi Mokoena  "})
    assert r.json()["user"]["name"] == "Thandi Mokoena"


def test_migrations_add_columns_missing_on_old_databases(tmp_path):
    """An install from before a column existed must still start: the safety net adds it."""
    from sqlalchemy import create_engine, inspect, text

    from cbx.migrate import run_migrations

    eng = create_engine(f"sqlite:///{(tmp_path / 'old.sqlite3').as_posix()}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255), name VARCHAR(160), role VARCHAR(16), password_hash VARCHAR(255))"))
        c.execute(text("INSERT INTO users (id, email, name, role, password_hash) VALUES (1, 'old@test.local', 'Old', 'user', '')"))
    run_migrations(eng)
    cols = {col["name"] for col in inspect(eng).get_columns("users")}
    assert {"prefs", "locked", "failed_logins", "reset_requested_at", "must_change_password", "is_axl"} <= cols
    with eng.begin() as c:
        row = c.execute(text("SELECT locked, failed_logins, is_active FROM users WHERE id = 1")).one()
    assert (row[0], row[1], row[2]) == (0, 0, 1)  # model defaults applied to the existing row
    run_migrations(eng)  # idempotent


# ----------------------------------------------------------------------------- manager role


def _manager_client(owner, email="mgr@test.local", name="Mpho Manager", team="Utilities billing", tools=None):
    """A manager on `team`, signed in. Owners and admins may create managers; the fixture owner does."""
    from tests.conftest import _make, login

    if tools is None:
        tools = [t["id"] for t in owner.get("/api/users").json()["tools"] if t["slug"] == "doc-watch"]
    pw = _make(owner, email, name, "manager", tools=tools, team=team)
    c = _fresh_client()
    login(c, email, pw)
    return c


def test_manager_role_is_granted_by_owner_and_admin_only(owner, admin, user):
    from tests.conftest import _uid

    # an admin may make a manager (and a user), never an admin
    r = admin.post("/api/users", json={"email": "m-by-admin@test.local", "name": "M", "role": "manager", "team_name": "Finance"})
    assert r.status_code == 200, r.text
    assert admin.post("/api/users", json={"email": "a-by-admin@test.local", "name": "A", "role": "admin"}).status_code == 403
    # an owner can promote a user to manager and back
    uid = _uid(owner, "thandi@test.local")
    assert owner.patch(f"/api/users/{uid}", json={"role": "manager"}).status_code == 200
    assert owner.get("/api/users").json()["counts"]["manager"] >= 1
    assert owner.patch(f"/api/users/{uid}", json={"role": "user"}).status_code == 200
    # a plain user still can't get near the Users page
    assert user.get("/api/users").status_code == 403
    assert user.post("/api/users", json={"email": "z@test.local", "name": "Z", "role": "user"}).status_code == 403


def test_manager_creates_users_on_own_team_only(owner):
    from tests.conftest import _uid

    mgr = _manager_client(owner)
    me = mgr.get("/api/auth/me").json()
    assert me["user"]["role"] == "manager"
    assert me["user"]["team"] == "Utilities billing"
    # tools by grant, like a user — only what the owner gave
    assert [t["slug"] for t in me["tools"]] == ["doc-watch"]
    # creates a user: lands on the manager's team whatever the request says
    r = mgr.post("/api/users", json={"email": "mgr-user-1@test.local", "name": "One", "role": "user"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["team"] == "Utilities billing" and r.json()["temp_password"]
    other_team = next(t for t in owner.get("/api/users").json()["teams"] if t["name"] != "Utilities billing")
    r = mgr.post("/api/users", json={"email": "mgr-user-2@test.local", "name": "Two", "role": "user", "team_id": other_team["id"]})
    assert r.status_code == 403
    r = mgr.post("/api/users", json={"email": "mgr-user-3@test.local", "name": "Three", "role": "user", "team_name": "Brand new team"})
    assert r.status_code == 403
    # never a manager, admin or owner
    for role in ("manager", "admin", "owner"):
        assert mgr.post("/api/users", json={"email": f"mgr-{role}@test.local", "name": "X", "role": role}).status_code == 403
    blocked = owner.get("/api/audit?action=role_grant_blocked").json()["rows"]
    assert any(b["actor"] == "mgr" for b in blocked)
    # sees only their team on Users & roles; can edit only their team's users
    lst = mgr.get("/api/users").json()
    # Roles are ranked now, so a manager may hand out anything BELOW their own rank — today that is
    # "user" and the shipped "viewer" preset. What matters is what they still cannot reach.
    assert lst["scope"]["team"] == "Utilities billing"
    assert "user" in lst["me_can_create"]
    assert not ({"manager", "admin", "owner"} & set(lst["me_can_create"]))
    assert all(u["team"] == "Utilities billing" for u in lst["users"])
    assert not any(u["is_axl"] for u in lst["users"])
    # The list shows what they may grant plus their own role; "viewer" is the shipped preset, which
    # ranks below a manager and is therefore theirs to hand out.
    assert {"manager", "user"} <= set(lst["counts"])
    admin_id = _uid(owner, "admin@test.local")  # the fixture admin sits on this team, but admins are not the manager's business
    assert not any(u["id"] == admin_id for u in lst["users"])
    # can grant only tools they hold themselves
    new_id = _uid(owner, "mgr-user-1@test.local")
    tools = owner.get("/api/users").json()["tools"]
    dw = next(t["id"] for t in tools if t["slug"] == "doc-watch")
    pdf = next(t["id"] for t in tools if t["slug"] == "pdf-studio")
    assert mgr.post("/api/users/access", json={"user_id": new_id, "tool_id": dw, "granted": True}).status_code == 200
    assert mgr.post("/api/users/access", json={"user_id": new_id, "tool_id": pdf, "granted": True}).status_code == 403
    assert mgr.post("/api/users", json={"email": "mgr-user-4@test.local", "name": "Four", "role": "user", "tools": [pdf]}).status_code == 403


def test_manager_edits_own_team_only_and_never_roles_or_teams(owner, admin):
    from tests.conftest import _make, _uid

    mgr = _manager_client(owner)
    _make(owner, "team-user@test.local", "Team User", "user", team="Utilities billing")
    _make(owner, "far-user@test.local", "Far User", "user", team="Finance")
    near, far = _uid(owner, "team-user@test.local"), _uid(owner, "far-user@test.local")
    admin_id, owner_id = _uid(owner, "admin@test.local"), _uid(owner, "owner@test.local")
    assert mgr.patch(f"/api/users/{near}", json={"title": "Analyst"}).status_code == 200
    assert mgr.post(f"/api/users/{near}/temp-password").status_code == 200
    assert mgr.patch(f"/api/users/{near}", json={"unlock": True}).status_code == 200
    assert mgr.patch(f"/api/users/{far}", json={"title": "Hacked"}).status_code == 403
    assert mgr.post(f"/api/users/{far}/temp-password").status_code == 403
    assert mgr.patch(f"/api/users/{admin_id}", json={"title": "x"}).status_code == 403
    assert mgr.patch(f"/api/users/{owner_id}", json={"is_active": False}).status_code == 403
    assert mgr.patch(f"/api/users/{near}", json={"role": "manager"}).status_code == 403
    assert mgr.patch(f"/api/users/{near}", json={"role": "admin"}).status_code == 403
    other_team = next(t["id"] for t in owner.get("/api/users").json()["teams"] if t["name"] == "Finance")
    assert mgr.patch(f"/api/users/{near}", json={"team_id": other_team}).status_code == 403
    # no teams, no row rules
    assert mgr.post("/api/users/teams", json={"name": "Shadow"}).status_code == 403
    assert mgr.get("/api/users/rules").status_code == 403
    assert mgr.post("/api/users/rules", json={"table": "watched_docs", "field": "client", "value": "Absa", "user_id": near}).status_code == 403
    scoped = owner.get("/api/audit?action=user_scope_blocked").json()["rows"]
    assert scoped and all(r["outcome"] == "blocked" for r in scoped)


def test_manager_is_not_an_admin_anywhere_else(owner):
    mgr = _manager_client(owner)
    for path in ("/api/requests/queue", "/api/develop/tasks", "/api/warehouse/tree", "/api/warehouse/stats", "/api/tickets", "/api/cexa/proposals", "/api/cexa/status", "/api/cexa/fixes", "/api/versions", "/api/versions/sandboxes", "/api/cexa/journal", "/api/cexa/agents"):
        assert mgr.get(path).status_code == 403, path
    assert mgr.post("/api/cexa/chat", json={"text": "hi"}).status_code == 403
    # the drawer works, team-scoped, with no hands
    s = mgr.get("/api/cexa/suggestions?route=users").json()["suggestions"]
    assert s and any("my team" in x["q"] for x in s)
    r = mgr.post("/api/cexa/ask", json={"question": "Please rename template harvest to Capitec", "route": "users"})
    assert r.status_code == 200, r.text
    assert r.json()["thinking"] == "" and r.json()["actions"] == []
    # the home counts a manager gets are the user's (no admin numbers)
    r = mgr.get("/api/home/counts")
    assert r.status_code == 200 and "agents_running" not in r.json()


def test_manager_audit_is_team_only(owner, admin):
    from tests.conftest import _make, login

    mgr = _manager_client(owner)
    pw = _make(owner, "audited@test.local", "Audited One", "user", team="Utilities billing")
    c = _fresh_client()
    login(c, "audited@test.local", pw)
    rows = mgr.get("/api/audit?limit=2000").json()
    assert rows["scope"] == "team" and rows["rows"]
    actors = {r["actor"] for r in rows["rows"]}
    assert "audited" in actors and "mgr" in actors
    assert "owner" not in actors and "admin" not in actors and not any(a.startswith("axl") for a in actors)
    assert not any(r["actor"] == "" or r["actor"] == "system" for r in rows["rows"])
    csv_actors = {line.split(",")[1] for line in mgr.get("/api/audit/export.csv").text.splitlines()[1:] if line.strip()}
    assert "audited" in csv_actors and csv_actors <= actors
    assert owner.get("/api/audit").json()["scope"] == "all"


def test_manager_sees_and_manages_team_docs_only(owner, admin, user):
    """RLS for Entity Compliance: a manager's register is their team's, no more."""
    from tests._pdfs import certificate

    mgr = _manager_client(owner)
    mine = user.post("/api/docwatch", json={"title": "Team fire cert", "entity": "Rosebank", "expiry": "2030-01-01"}).json()  # thandi is on Utilities billing
    # the owner sits on Digital & Transformation — another team (the fixture admin is on Utilities billing, so their lines ARE the manager's team's)
    theirs = owner.post("/api/docwatch/upload", files=[("files", ("a.pdf", certificate(site="Nedbank Park"), "application/pdf"))], data={"entity": "Nedbank Park"}).json()["docs"][0]
    ids = {d["id"] for d in mgr.get("/api/docwatch").json()["docs"]}
    assert mine["id"] in ids and theirs["id"] not in ids
    assert mgr.patch(f"/api/docwatch/{mine['id']}", json={"warn_days": 45}).status_code == 200
    assert mgr.patch(f"/api/docwatch/{theirs['id']}", json={"warn_days": 45}).status_code == 403
    assert mgr.get(f"/api/docwatch/{theirs['id']}/download").status_code == 403
    # a manager may delete a team line (an admin may delete any); a user still can't
    assert user.delete(f"/api/docwatch/{mine['id']}").status_code == 403
    assert mgr.delete(f"/api/docwatch/{theirs['id']}").status_code == 403
    assert mgr.delete(f"/api/docwatch/{mine['id']}").status_code == 200
    assert admin.delete(f"/api/docwatch/{theirs['id']}").status_code == 200


# ----------------------------------------------------------------------------- branding


def test_branding_public_and_owner_editable(owner, admin, tmp_path):
    from cbx import branding as B

    anon = _fresh_client()
    b = anon.get("/api/branding").json()
    assert b["product"] and b["org"] and b["ai_name"] == "C-EXA" and "has_logo" in b and "has_favicon" in b and b["version"]
    assert anon.get("/api/branding/logo").status_code == 404 or b["has_logo"]
    # only an owner changes it
    assert admin.patch("/api/branding", json={"product": "Nope"}).status_code == 403
    assert anon.patch("/api/branding", json={"product": "Nope"}).status_code == 401
    assert owner.patch("/api/branding", json={"accent": "green"}).status_code == 400
    assert owner.patch("/api/branding", json={"ai_name": "<script>"}).status_code == 400
    try:
        r = owner.patch("/api/branding", json={"product": "CBX-Connect", "tagline": "Digital & Transformation", "ai_name": "HRMS", "accent": "#0B8A63"})
        assert r.status_code == 200, r.text
        assert r.json()["ai_name"] == "HRMS" and r.json()["accent"] == "#0B8A63"
        me = owner.get("/api/auth/me").json()
        assert me["ai_name"] == "HRMS" and me["product"] == "CBX-Connect" and me["tagline"] == "Digital & Transformation" and me["model"].startswith("HRMS")
        # the AI's own account follows the name, and his prompts do too
        cexa = next(u for u in owner.get("/api/users").json()["users"] if u["is_axl"])
        assert cexa["name"] == "HRMS"
        from cbx.cexa.persona import persona

        assert "You are HRMS" in persona() and "C-EXA" not in persona()
        # a logo: PNG accepted, garbage refused, served publicly, removable
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        assert owner.post("/api/branding/logo", files=[("files", ("logo.png", png, "image/png"))]).json()["has_logo"] is True
        assert anon.get("/api/branding/logo").status_code == 200 and anon.get("/api/branding/logo").headers["content-type"].startswith("image/png")
        assert owner.post("/api/branding/favicon", files=[("files", ("x.txt", b"hello", "text/plain"))]).status_code == 400
        assert owner.post("/api/branding/favicon", files=[("files", ("evil.svg", b"<svg onload='alert(1)'></svg>", "image/svg+xml"))]).status_code == 400
        assert owner.delete("/api/branding/logo").json()["has_logo"] is False
        assert anon.get("/api/branding/logo").status_code == 404
        assert any(r["action"] == "branding_changed" for r in owner.get("/api/audit").json()["rows"])
    finally:
        owner.patch("/api/branding", json={"product": "", "tagline": "", "ai_name": "", "accent": ""})
        owner.delete("/api/branding/logo")
    assert B.ai_name() == "C-EXA"
    assert next(u for u in owner.get("/api/users").json()["users"] if u["is_axl"])["name"] == "C-EXA"


def test_a_wrong_password_lock_lifts_itself_and_the_last_owner_is_never_locked(owner, db, client):
    """16 Sep 2026: the only owner hit 5 wrong passwords and the app told him to ask the owner.
    Two fixes: the lock expires on its own, and the last owner standing cannot be locked at all."""
    from datetime import timedelta

    from cbx.db import utcnow
    from cbx.models import User
    from cbx.security.auth import LoginError, verify_login
    from cbx.config import settings
    from tests.conftest import STRONG

    me = db.query(User).filter(User.role == "owner", User.is_axl.is_(False)).one()

    # the last owner: five wrong passwords do NOT lock the account
    for _ in range(settings.login_max_attempts + 2):
        try:
            verify_login(db, me.email, "not-my-password")
        except LoginError:
            pass
    db.flush()
    assert me.locked is False and me.failed_logins > settings.login_max_attempts

    # a second owner exists → now the first one can be locked
    other = User(email="second.owner@test.local", name="Second Owner", role="owner", password_hash="x")
    db.add(other)
    db.flush()
    me.failed_logins = 0
    for _ in range(settings.login_max_attempts):
        try:
            verify_login(db, me.email, "not-my-password")
        except LoginError:
            pass
    db.flush()
    assert me.locked is True and me.locked_at is not None

    # while the wait runs, the message says how long — not "ask the owner"
    try:
        verify_login(db, me.email, "not-my-password")
        raise AssertionError("should have raised")
    except LoginError as e:
        assert "Try again in" in str(e)

    # once the wait is over the lock lifts itself and a good password works
    me.locked_at = utcnow() - timedelta(minutes=settings.login_lockout_minutes + 1)
    db.flush()
    u = verify_login(db, me.email, STRONG)
    assert u.id == me.id and me.locked is False and me.locked_at is None and me.failed_logins == 0

    # a lock set by hand (no locked_at) still needs a person
    me.locked, me.locked_at = True, None
    db.flush()
    try:
        verify_login(db, me.email, STRONG)
        raise AssertionError("should have raised")
    except LoginError as e:
        assert "unlock" in str(e)
    me.locked = False
    db.delete(other)
    db.flush()


def test_an_audit_row_is_written_once_even_when_the_endpoint_commits_then_raises(client, owner_pw, db):
    """Sign-in commits for itself (to keep the failed-attempt counter) and then raises a 401. The
    rollback-keeping-audit path then found the already-committed row still on its list and wrote it
    again — same row, same timestamp, two ids. Every count built on the audit trail was double,
    including the overnight briefing C-EXA sends."""
    from cbx.models import AuditLog

    before = db.query(AuditLog).filter(AuditLog.action == "login_failed").count()
    client.post("/api/auth/login", json={"email": "owner@test.local", "password": "not-the-password"})
    db.expire_all()
    rows = db.query(AuditLog).filter(AuditLog.action == "login_failed").order_by(AuditLog.id.desc()).limit(2).all()
    assert db.query(AuditLog).filter(AuditLog.action == "login_failed").count() == before + 1
    if len(rows) == 2:
        assert rows[0].at != rows[1].at  # the tell-tale: two rows sharing a timestamp to the microsecond


def test_a_blocked_action_is_still_recorded_when_the_request_is_rolled_back(owner, user, db):
    """The other half of the same machinery: a refused request rolls back, but the audit row saying
    it was refused has to survive. Fixing the double-write must not lose these."""
    from cbx.models import AuditLog

    before = db.query(AuditLog).filter(AuditLog.outcome == "blocked").count()
    assert user.get("/api/audit").status_code == 403
    db.expire_all()
    assert db.query(AuditLog).filter(AuditLog.outcome == "blocked").count() >= before


def test_migrations_are_idempotent(db):
    from cbx.db import engine
    from cbx.migrate import run_migrations

    from sqlalchemy import inspect, text

    run_migrations(engine)
    run_migrations(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("tools")}
    assert {"kind", "ticket_id", "config"} <= cols
    assert "origin" in {c["name"] for c in inspect(engine).get_columns("requests")}
    with engine.connect() as c:
        ids = {r[0] for r in c.execute(text("SELECT id FROM schema_migrations"))}
    assert {"0003_requests_origin", "0004_tools_kind", "0005_tools_ticket_id", "0006_tools_config"} <= ids
    # and on a database that predates the columns, the ALTERs run once and only once
    from sqlalchemy import create_engine

    old = create_engine("sqlite://")
    with old.begin() as c:
        c.execute(text("CREATE TABLE requests (id INTEGER PRIMARY KEY)"))
        c.execute(text("CREATE TABLE tickets (id INTEGER PRIMARY KEY)"))
        c.execute(text("CREATE TABLE tools (id INTEGER PRIMARY KEY)"))
    run_migrations(old)
    run_migrations(old)
    assert "origin" in {c["name"] for c in inspect(old).get_columns("requests")}
    assert {"kind", "ticket_id", "config"} <= {c["name"] for c in inspect(old).get_columns("tools")}


def test_the_owner_can_name_the_ai_axl(owner):
    """1.0.56: a left-over AXL (the old default) still means C-EXA, but an AXL the owner types stays AXL."""
    from cbx import branding as B

    try:
        r = owner.patch("/api/branding", json={"ai_name": "AXL"})
        assert r.status_code == 200, r.text
        assert B.ai_name() == "AXL"
        assert owner.get("/api/branding").json().get("ai_name", "AXL") == "AXL"
        # a saved name from before 1.0.56 (no mark that the owner chose it) is still read as C-EXA
        o = dict(B._overrides()); o.pop("ai_name_chosen", None); B._write(o)
        assert B.ai_name() == "C-EXA"
    finally:
        owner.patch("/api/branding", json={"ai_name": ""})
    assert B.ai_name() == "C-EXA"
