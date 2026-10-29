"""To-do: the built-in page and its API went in 1.0.79 — the To-do tool folder (tools/todo, tests in
tools/todo/tests) has replaced them since 1.0.55. What stays: the old tables' migration and C-EXA's hands."""


def test_cexa_works_in_the_builder_todo_not_the_old_one():
    """1.0.61: C-EXA's to-do hands moved to the Builder To-do tool (tests/test_cexa_builder_hands.py)."""
    from cbx.cexa import tools as T

    assert not {"add_todo", "list_my_todo", "todo_team_summary", "list_jobs", "job_summary"} & set(T.TOOLS)
    assert {"give_task", "my_tasks"} <= set(T.TOOLS)


def test_migration_makes_todo_team_id_nullable(tmp_path):
    """An install that promoted 1.0.22 has NOT NULL team_id on the to-do tables; the migration
    rebuilds them so temp-job boards (team_id NULL) can be created without losing rows."""
    import sqlite3
    from sqlalchemy import create_engine, inspect, text
    from cbx.migrate import _rebuild_if_notnull
    p = tmp_path / "old.sqlite3"
    con = sqlite3.connect(p)
    con.executescript("""
      CREATE TABLE todo_columns (id INTEGER PRIMARY KEY, team_id INTEGER NOT NULL, name VARCHAR(80), "order" INTEGER, is_done BOOLEAN);
      INSERT INTO todo_columns (id, team_id, name, "order", is_done) VALUES (1, 7, 'To do', 0, 0), (2, 7, 'Done', 2, 1);
    """)
    con.commit(); con.close()
    eng = create_engine(f"sqlite:///{p}")
    with eng.begin() as c:
        _rebuild_if_notnull(c, "todo_columns", "team_id")
        cols = {x["name"]: x for x in inspect(c).get_columns("todo_columns")}
        assert cols["team_id"]["nullable"] is True and "job_id" in cols
        rows = c.execute(text('SELECT id, team_id, name FROM todo_columns ORDER BY id')).fetchall()
        assert [tuple(r) for r in rows] == [(1, 7, "To do"), (2, 7, "Done")]
        _rebuild_if_notnull(c, "todo_columns", "team_id")  # idempotent
