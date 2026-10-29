"""1.0.55: the old To-do rows are copied into the To-do tool folder once, keeping who added them and when."""
from datetime import date, datetime, timedelta, timezone

from cbx.config import ROOT
from cbx.engine import loader
from cbx.engine import store as STORE


def test_old_todo_rows_are_copied_in_once(owner, db, tmp_path, monkeypatch):
    from cbx import state
    from cbx.models import AppState, User
    from cbx.models.todo import TodoGoal, TodoJob, TodoJobMember, TodoRewardClaim, TodoTask
    from cbx.todo import copy_in

    loader.reload(ROOT / "tools")
    monkeypatch.setattr(STORE, "path_for", lambda slug: tmp_path / f"{slug}.sqlite")
    db.query(AppState).filter(AppState.key == copy_in.KEY).delete()
    before = {M: db.query(M).count() for M in (TodoRewardClaim, TodoGoal, TodoJobMember, TodoTask, TodoJob)}  # other tests' old rows are copied too
    me = db.query(User).filter(User.email == "owner@test.local").one()
    from cbx.models import Team

    team = db.get(Team, me.team_id) if me.team_id else None
    if team is None:
        team = Team(name="Copy-in team", slug="copy-in-team")
        db.add(team)
        db.flush()
    then = datetime(2026, 3, 2, 9, 30, tzinfo=timezone.utc)
    db.add_all([
        TodoTask(title="Old personal", kind="personal", created_by_id=me.id, assignee_id=me.id, steps=[{"id": "a", "text": "one", "done": True}, {"id": "b", "text": "two"}], created_at=then),
        TodoTask(title="Old team open", kind="team", created_by_id=me.id, assignee_id=None, team_id=team.id),
        TodoTask(title="Old done", kind="personal", created_by_id=me.id, assignee_id=me.id, status="done", completed_at=then + timedelta(days=1)),
        TodoTask(title="Dropped", kind="personal", created_by_id=me.id, status="dropped"),
    ])
    job = TodoJob(title="Old job", lead_id=me.id, created_by_id=me.id)
    db.add(job)
    db.flush()
    db.add_all([TodoJobMember(job_id=job.id, user_id=me.id, status="active"), TodoTask(title="On the job", kind="team", job_id=job.id, created_by_id=me.id, assignee_id=me.id)])
    goal = TodoGoal(team_id=team.id, title="Old goal", target=3, complexity="long", period_start=date(2026, 3, 1), period_end=date(2026, 3, 31), reward_text="Pizza", created_by_id=me.id)
    db.add(goal)
    db.flush()
    db.add(TodoRewardClaim(goal_id=goal.id, user_id=me.id, day=date(2026, 3, 20)))
    db.flush()

    got = copy_in.run_at_start(db)
    assert got["jobs"] == before[TodoJob] + 1 and got["goals"] == db.query(TodoGoal).filter(TodoGoal.active.is_(True)).count() and got["reward_days"] >= 1
    assert got["job_members"] == before[TodoJobMember] + 1 and got["tasks"] + got["job_tasks"] >= 4
    t = loader.registry().tools["todo"]
    st = STORE.Store("todo")
    rows = {r["title"]: r for r in st.rows("tasks", t.data["tasks"]["columns"], limit=2000)["rows"]}
    p = rows["Old personal"]
    assert p["kind"] == "Mine" and p["who"] == me.id and p["created_by"] == me.id and p["created_at"].startswith("2026-03-02") and p["steps_n"] == 2
    assert rows["Old team open"]["kind"] == "Team" and rows["Old team open"]["who"] is None  # still open for taking
    assert rows["Old done"]["status"] == "Done" and rows["Old done"]["finished"] == "2026-03-03" and rows["Old done"]["done_by"] == me.id
    assert "Dropped" not in rows
    g = next(x for x in st.rows("goals", t.data["goals"]["columns"])["rows"] if x["title"] == "Old goal")
    assert g["size"] == "Complex" and g["reward"] == "Pizza" and g["ends"] == "2026-03-31"
    assert any(r["goal"] == g["id"] and r["day"] == "2026-03-20" for r in st.rows("reward_days", t.data["reward_days"]["columns"])["rows"])
    assert any(r["title"] == "On the job" for r in st.rows("job_tasks", t.data["job_tasks"]["columns"])["rows"])
    # never twice
    assert copy_in.run_at_start(db) == {} and state.get(db, copy_in.KEY)
    db.query(AppState).filter(AppState.key == copy_in.KEY).delete()
    assert copy_in.run_at_start(db) == {}  # the folder has rows already: not copied again
    assert "already had rows" in state.get(db, copy_in.KEY)
    assert st.rows("tasks", t.data["tasks"]["columns"])["total"] == got["tasks"]
    db.rollback()
