"""1.0.65: the office view's desks — per agent, how much work it has had (the filing rack) and how much it
finished in the last 30 days (the plant)."""
from datetime import timedelta

from cbx.cexa import teams as TM
from cbx.db import utcnow
from cbx.models import AgentWork, TeamMember


def test_now_counts_each_desk(owner, db):
    db.query(AgentWork).delete()
    TM.ensure_teams(db)
    m = db.query(TeamMember).order_by(TeamMember.id).first()
    for st, days in (("done", 2), ("done", 40), ("open", None), ("failed", 1)):
        db.add(AgentWork(kind="task", title=f"OFFICE {st}", to_member_id=m.id, status=st, from_label="t", source="owner",
                         finished_at=utcnow() - timedelta(days=days) if days else None))
    db.add(AgentWork(kind="task", title="OFFICE ceo", to_member_id=None, status="done", from_label="t", source="owner", finished_at=utcnow()))
    db.commit()
    try:
        d = owner.get("/api/cexa/now").json()["desks"]
        assert d[m.name] == {**d[m.name], "total": 4, "open": 1, "done": 2, "done_30d": 1}
        assert d[m.name]["last_done"]
        assert sum(v["total"] for k, v in d.items() if k != m.name) == 1  # C-EXA's own desk
    finally:
        db.query(AgentWork).delete()
        db.commit()
