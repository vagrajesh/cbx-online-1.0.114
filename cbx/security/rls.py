"""Row-level security. Two layers:

1. Ownership scoping — every query that returns user-owned things (requests, batches,
   folders, files, messages) goes through `scope()` which adds the owner/team predicate
   unless the caller is admin/owner. Admins see everything — Edwin's model is owner/admin/user,
   and admins build for everyone. A MANAGER sees their own rows plus, on tables that carry a
   `team_id`, the rows of their team (pass `team_attr`); on tables without one they are a user.
2. Row rules — for shared data (master-sheet rows, watched docs) a user/team may be
   limited to rows where field op value (e.g. client = 'Absa'). `row_allowed()` is the
   single place that evaluates them. Rules are structured, never SQL.

When running on Postgres, `policies.sql` mirrors layer 1 in the database itself so a bug
in the app layer still cannot leak rows (see cbx/security/policies.sql).
"""
from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from ..models import RowRule, User


def scope(q: Query, model, user: User, owner_attr: str = "user_id", team_attr: str | None = None) -> Query:
    if user.is_admin or user.is_axl:
        return q
    own = getattr(model, owner_attr) == user.id
    if user.is_manager and team_attr and user.team_id and hasattr(model, team_attr):
        return q.filter(or_(own, getattr(model, team_attr) == user.team_id))
    return q.filter(own)


def rules_for(db: Session, user: User, table: str) -> list[RowRule]:
    q = db.query(RowRule).filter(RowRule.table == table)
    conds = [RowRule.user_id == user.id]
    if user.team_id:
        conds.append(RowRule.team_id == user.team_id)
    return q.filter(or_(*conds)).all()


def _match(rule: RowRule, row: dict[str, Any]) -> bool:
    v = row.get(rule.field)
    if v is None:
        v = row.get("_" + rule.field)
    sv = "" if v is None else str(v)
    if rule.op == "eq":
        return sv.lower() == rule.value.lower()
    if rule.op == "in":
        return sv.lower() in [x.strip().lower() for x in rule.value.split(",")]
    if rule.op == "startswith":
        return sv.lower().startswith(rule.value.lower())
    return False


def row_allowed(rules: list[RowRule], row: dict[str, Any]) -> bool:
    """No rules → everything (admins) or, for users, only rows they own/share. With rules →
    a row must satisfy at least one rule (rules are grants, combined with OR)."""
    if not rules:
        return True
    return any(_match(r, row) for r in rules)


def _mine(user: User, r: dict[str, Any]) -> bool:
    """A row the person owns (`_by`) — or, for a manager, a row of their team (`_team`)."""
    if r.get("_by") == user.id:
        return True
    return bool(user.is_manager and user.team_id and r.get("_team") == user.team_id)


def filter_rows(db: Session, user: User, table: str, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    if user.is_admin or user.is_axl:
        return list(rows)
    rules = rules_for(db, user, table)
    if not rules:
        # Deny by default for shared tables: a user with no rule sees only rows they created
        # (a manager: rows their team created).
        return [r for r in rows if _mine(user, r)]
    return [r for r in rows if row_allowed(rules, r) or _mine(user, r)]
