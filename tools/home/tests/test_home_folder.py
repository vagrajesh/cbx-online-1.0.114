"""Home as a tool folder: sound, one page per role, numbers that respect roles and tools, and a
personal layout that is the person's own."""
from cbx.engine import loader, metrics


def test_folder_is_sound():
    t = loader.registry().tools.get("home")
    assert t is not None and t.ok, (t.errors if t else "not loaded")
    assert set(t.pages) == {"owner", "admin", "manager", "user"}
    assert t.manifest["access"]["everyone"] and t.manifest["menu"]["hidden"]
    for name, p in t.pages.items():
        for w in loader.widgets_of(name, p):
            if w["type"] == "metrics":
                assert all(i["metric"] in metrics.METRICS for i in w["items"]), name
    assert not any(t.missing().values()), t.missing()


def test_each_role_gets_its_page_and_the_menu_does_not_list_it(owner, user):
    assert not any(x["slug"] == "home" for x in owner.get("/api/engine/menu").json()["tools"])
    assert owner.get("/api/engine/home").json()["page"]["name"] == "owner"
    h = user.get("/api/engine/home").json()
    assert h["page"]["name"] == "user" and h["mine"] == {}
    keys = h["widgets"]["user_mine"]["metrics"]
    v = user.get("/api/engine/metrics?keys=" + ",".join(keys + ["people.active", "signin.failed_24h"])).json()["values"]
    assert "my.requests_open" in v and "people.active" not in v and "signin.failed_24h" not in v  # not for a user


def test_a_person_rearranges_their_own_home(user, owner):
    r = user.put("/api/engine/home/mine", json={"tiles": {"user_mine": ["my.todo_open", "my.requests_open", "people.active", "nope"]}, "hidden": ["user_batches", "not-a-block"]})
    assert r.status_code == 200
    mine = r.json()["mine"]
    assert mine["tiles"]["user_mine"] == ["my.todo_open", "my.requests_open", "people.active"]  # unknown keys dropped
    assert mine["hidden"] == ["user_batches"]
    assert user.get("/api/engine/home").json()["mine"] == mine
    assert "people.active" not in user.get("/api/engine/metrics?keys=people.active").json()["values"]  # saved, still not shown
    assert owner.get("/api/engine/home").json()["mine"] == {}  # the owner's Home is untouched
    assert user.put("/api/engine/home/mine", json={"reset": True}).json()["mine"] == {}
