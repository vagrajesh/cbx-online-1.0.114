"""Tiny forward-only migration runner. `create_all` handles new tables; entries here handle
column additions on existing installs so the version injector can upgrade a live box.
Each migration is (id, sql-or-callable). Applied ids are recorded in schema_migrations.

`add_column(table, column, ddl)` is idempotent: on a fresh install `create_all` has already
created the column, so the ALTER is skipped and only the migration id is recorded."""
from __future__ import annotations

import re
from typing import Callable

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine


def add_column(table: str, column: str, ddl: str) -> Callable[[Connection], None]:
    def run(c: Connection) -> None:
        insp = inspect(c)
        if not insp.has_table(table):  # very old install without the table: create_all made it already, or will
            return
        cols = {col["name"] for col in insp.get_columns(table)}
        if column not in cols:
            c.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))

    run.__name__ = f"add_column_{table}_{column}"
    return run


MIGRATIONS: list[tuple[str, str | Callable[[Connection], None]]] = [
    # ("0002_users_prefs", "ALTER TABLE users ADD COLUMN prefs TEXT DEFAULT '{}'"),
    ("0003_requests_origin", add_column("requests", "origin", "VARCHAR(8) DEFAULT 'user'")),
    ("0004_tools_kind", add_column("tools", "kind", "VARCHAR(16) DEFAULT 'builtin'")),
    ("0005_tools_ticket_id", add_column("tools", "ticket_id", "INTEGER REFERENCES tickets(id)")),
    ("0006_tools_config", add_column("tools", "config", "JSON DEFAULT '{}'")),
    # C-EXA's job title showed the model name to everyone on Users & roles; people see "C-EXA", never the model.
    ("0008_axl_messages_cleared", add_column("axl_messages", "cleared", "BOOLEAN DEFAULT 0")),
    ("0007_axl_title", lambda c: c.execute(text("UPDATE users SET title = 'Controller' WHERE is_axl = 1 AND title LIKE 'Controller%'")) if inspect(c).has_table("users") else None),
    # 1.0.21: the "manager" role is a new value in users.role (a plain string column — nothing to alter);
    # recorded so the schema history says when it arrived.
    ("0009_manager_role_ok", lambda c: None),
    # Entity Compliance: a register line can be marked "doesn't expire".
    ("0010_watched_docs_no_expiry", add_column("watched_docs", "no_expiry", "BOOLEAN DEFAULT 0")),
    # Entity Compliance becomes a grid (companies × document types). New tables come from create_all; the
    # cell columns on watched_docs are added here (add_missing_columns will have done it already — this
    # records it), then a fresh install gets its default columns and a live one has its free-text register
    # lines linked to companies and columns (cbx/docwatch/service.py: seed_default_types, migrate_legacy).
    ("0011_watched_docs_grid_cells", lambda c: [f(c) for f in (add_column("watched_docs", "entity_id", "INTEGER REFERENCES entities(id)"), add_column("watched_docs", "doc_type_id", "INTEGER REFERENCES doc_types(id)"), add_column("watched_docs", "required", "BOOLEAN DEFAULT 1"), add_column("watched_docs", "scan", "JSON DEFAULT '{}'"), add_column("watched_docs", "history", "JSON DEFAULT '[]'"), add_column("watched_docs", "updated_at", "DATETIME"))]),
    ("0012_docwatch_default_columns", lambda c: _docwatch(c, "seed_default_types")),
    ("0013_docwatch_link_legacy_lines", lambda c: _docwatch(c, "migrate_legacy")),
    # PDF Studio 1.0.21: per-column config (formula/optional reason) and re-run history
    ("0014_pdf_columns_config", add_column("pdf_columns", "config", "JSON DEFAULT '{}'")),
    ("0015_pdf_extractions_history", add_column("pdf_extractions", "history", "JSON DEFAULT '[]'")),
    # To-do 1.0.23: boards belong to a team OR a temp job, so team_id became nullable. SQLite cannot
    # drop NOT NULL in place — rebuild the two tables from the model when an older shape is found.
    ("0016_todo_team_nullable", lambda c: [_rebuild_if_notnull(c, t, "team_id") for t in ("todo_columns", "todo_tasks")]),
    # 1.0.27: C-EXA's proposals get a stable dedupe key (counts no longer baked into titles, so the same
    # problem can't open a second card). The backfill below keys every existing card from its old title.
    ("0017_proposals_dedupe_key", add_column("axl_proposals", "dedupe_key", "VARCHAR(160) DEFAULT ''")),
    # 1.0.27 Entity Compliance: a named responsible person per entity, shown as a grid column.
    # The backfill runs after the ALTER (entities created before this got "Responsible: X" in notes).
    ("0018_entities_responsible", lambda c: _entities_responsible(c)),
    # 1.0.41: device cookies are stored hashed, like the codes — rows from before hold the raw value
    ("0019_trusted_devices_hash_tokens", lambda c: _hash_device_tokens(c)),
    # 1.0.41: the model has had UNIQUE(entity_id, doc_type_id) on the grid cells for a while, but a
    # database created before that never got the index — two grid loads at once could make two cells
    ("0020_watched_docs_unique_cell", lambda c: _unique_grid_cells(c)),
    # 1.0.45: two-step verification — the person's method, sealed secret, recovery codes, wrong-code count,
    # an end date for outside accounts; a session that has passed the password but not the second step
    ("0021_users_mfa", add_column("users", "mfa_method", "VARCHAR(16) DEFAULT ''")),
    ("0021b_users_mfa_secret", add_column("users", "mfa_secret", "TEXT DEFAULT ''")),
    ("0021c_users_mfa_enrolled_at", add_column("users", "mfa_enrolled_at", "TIMESTAMP")),
    ("0021d_users_mfa_recovery", add_column("users", "mfa_recovery", "TEXT DEFAULT '[]'")),
    ("0021e_users_mfa_failed", add_column("users", "mfa_failed", "INTEGER DEFAULT 0")),
    ("0021f_users_expires_at", add_column("users", "expires_at", "TIMESTAMP")),
    ("0021g_sessions_pending_mfa", add_column("login_sessions", "pending_mfa", "BOOLEAN DEFAULT 0")),
    ("0022_sessions_view_as", add_column("login_sessions", "view_as_id", "INTEGER")),
    ("0022b_sessions_view_as_until", add_column("login_sessions", "view_as_until", "DATETIME")),
    ("0023_tickets_tool_slug", add_column("tickets", "tool_slug", "VARCHAR(64) DEFAULT ''")),
    # 1.0.50: C-EXA's standing teams — which team has a job, what kind of job, what it hands over
    ("0024_missions_team_key", add_column("cexa_missions", "team_key", "VARCHAR(32) DEFAULT 'build'")),
    ("0024b_missions_kind", add_column("cexa_missions", "kind", "VARCHAR(16) DEFAULT 'build'")),
    ("0024c_missions_document", add_column("cexa_missions", "document", "TEXT DEFAULT ''")),
    # 1.0.98 (the Support desk, from CBX-Connect 2.10.x): Accept and the SLA, the pictures Iris read, the SAP block,
    # who set the system, the lessons a ticket used, the readers' review. New tables (support_settings, support_notices,
    # support_lessons) come from create_all.
    ("1098_support_tickets_accept", lambda c: [f(c) for f in (
        add_column("support_tickets", "accepted_at", "DATETIME"), add_column("support_tickets", "accepted_by", "VARCHAR(160) DEFAULT ''"),
        add_column("support_tickets", "accept_nonce", "VARCHAR(64) DEFAULT ''"), add_column("support_tickets", "accept_hash", "VARCHAR(64) DEFAULT ''"),
        add_column("support_tickets", "assign_seq", "INTEGER DEFAULT 0"), add_column("support_tickets", "lead_ids", "JSON DEFAULT '[]'"))]),
    ("1098_support_tickets_reading", lambda c: [f(c) for f in (
        add_column("support_tickets", "seen", "JSON DEFAULT '[]'"), add_column("support_tickets", "sap", "JSON DEFAULT '{}'"),
        add_column("support_tickets", "system_set_by", "VARCHAR(8) DEFAULT ''"), add_column("support_tickets", "lessons", "JSON DEFAULT '[]'"),
        add_column("support_tickets", "review", "JSON DEFAULT '{}'"), add_column("support_tickets", "reader", "VARCHAR(32) DEFAULT ''"))]),
    # 1.0.103: a ticket the owner or an admin closed as not relevant (never counted as solved)
    ("1103_support_tickets_closed_as", add_column("support_tickets", "closed_as", "VARCHAR(32) DEFAULT ''")),
    # 1.0.104: add_missing_columns adds ap_captures.portfolio on an upgrade but not its index (create_all makes it on a new install)
    ("1104_ap_captures_portfolio_index", lambda c: c.execute(text("CREATE INDEX IF NOT EXISTS ix_ap_captures_portfolio ON ap_captures (portfolio)"))
     if inspect(c).has_table("ap_captures") and "portfolio" in {col["name"] for col in inspect(c).get_columns("ap_captures")} else None),
    # 1.0.109: a removed ticket; the pictures or files a mail left out (and why); the admins' "Automatic mails"
    ("1109_support_flow", lambda c: [f(c) for f in (
        add_column("support_tickets", "removal", "JSON DEFAULT '{}'"), add_column("support_messages", "left_out", "JSON DEFAULT '[]'"),
        add_column("support_mail", "why", "VARCHAR(200) DEFAULT ''"), add_column("support_mail", "text", "TEXT DEFAULT ''"),
        add_column("support_messages", "mail_ref", "INTEGER"))]),
    # 1.0.109 (round 2): a reply written in the editor, pictures where they were pasted (cbx/support/rich.py)
    ("1109_support_rich", add_column("support_messages", "html", "TEXT DEFAULT ''")),
    # 1.0.109 (round 2, Flow): the responsible person, the follow-up clock, AXL's auto-assign (support_picks comes from create_all)
    ("1109_support_r2_flow", lambda c: [f(c) for f in (
        add_column("support_tickets", "owner", "JSON DEFAULT '{}'"), add_column("support_tickets", "followup", "JSON DEFAULT '{}'"),
        add_column("support_tickets", "waiting_since", "DATETIME"), add_column("support_tickets", "auto", "JSON DEFAULT '{}'"),
        lambda c2: c2.execute(text("CREATE INDEX IF NOT EXISTS ix_support_tickets_waiting_since ON support_tickets (waiting_since)"))
        if inspect(c2).has_table("support_tickets") else None)]),
    # 1.0.111 (T-0087): a reply judged a "new problem" on a ticket that was still open is added to its ticket after all (no mail)
    ("1111_support_new_problem_undone", lambda c: _support_new_problem_undone(c)),
    # 1.0.113: authenticator codes against a drifting server clock (the learned difference, the last step used), and
    # second steps that were proven; then, once: every email-codes account is marked unproven (they were switched
    # without a code), so its next sign-in from any browser asks for an emailed code
    ("1113_users_mfa_drift", lambda c: [f(c) for f in (
        add_column("users", "mfa_drift", "INTEGER DEFAULT 0"), add_column("users", "mfa_last_step", "INTEGER DEFAULT 0"),
        add_column("users", "mfa_proven_at", "TIMESTAMP"))]),
    ("1113_mfa_email_unproven", lambda c: _mfa_email_unproven(c)),
    # 1.0.113 (security review): a code says what it is for (existing rows are sign-in codes); when a set-up began.
    # The sign_in_tickets table comes from create_all.
    ("1113_login_codes_purpose", lambda c: [f(c) for f in (
        add_column("login_codes", "purpose", "VARCHAR(16) DEFAULT 'signin'"), add_column("users", "mfa_setup_at", "TIMESTAMP"),
        lambda c2: c2.execute(text("UPDATE login_codes SET purpose = 'signin' WHERE purpose IS NULL OR purpose = ''"))
        if inspect(c2).has_table("login_codes") else None)]),
    # 1.0.114: the top-down company — work belongs to a goal, a team, a level of the goal's tree (cexa_goals and
    # cexa_precedents come from create_all)
    ("1114_cexa_work_goals", lambda c: [f(c) for f in (
        add_column("cexa_work", "goal_id", "INTEGER"), add_column("cexa_work", "team_id", "INTEGER"),
        add_column("cexa_work", "level", "VARCHAR(8) DEFAULT ''"), add_column("cexa_work", "depends", "JSON DEFAULT '[]'"),
        add_column("cexa_work", "done_when", "TEXT DEFAULT ''"), add_column("cexa_work", "attempts", "INTEGER DEFAULT 0"),
        add_column("cexa_work", "blocker", "TEXT DEFAULT ''"),
        lambda c2: c2.execute(text("CREATE INDEX IF NOT EXISTS ix_cexa_work_goal_id ON cexa_work (goal_id)"))
        if inspect(c2).has_table("cexa_work") else None)]),
    # 1.0.114 (review): the AI's account stops being an owner — an admin whose never-alone caps are refused in code
    # (cbx/models/core.py NEVER_ALONE_CAPS). Once; written to the audit trail with the account's id.
    ("1114_axl_not_owner", lambda c: _axl_not_owner(c)),
    # R8 (test/R8-Decision.md): "limited to a team" and "manages the team" become two things. The account's own
    # limit, the manager half kept by every role that had it, the limit put on every account a team-limited person
    # made (D3), and the roles they built without the limit listed for the owner — not changed (D4).
    ("1114_r8_users_team_limited", add_column("users", "team_limited", "BOOLEAN DEFAULT 0")),
    ("1114_r8_team_lead", lambda c: _r8_team_lead(c)),
    ("1114_r8_team_limited_accounts", lambda c: _r8_team_limited_accounts(c)),
    ("1114_r8_roles_review", lambda c: _r8_roles_review(c)),
]


def _r8_audit(c: Connection, action: str, target: str, detail: str, outcome: str = "ok") -> None:
    if inspect(c).has_table("audit_log"):
        c.execute(text("INSERT INTO audit_log (at, actor_id, actor_label, action, target, detail, outcome, meta, ip) "
                       "VALUES (CURRENT_TIMESTAMP, NULL, 'system', :a, :t, :d, :o, '{}', '')"),
                  {"a": action, "t": target[:255], "d": detail, "o": outcome})


def _r8_roles(c: Connection) -> list[tuple[str, str, list, int | None]]:
    """(slug, name, caps, created_by_id) of every role; caps is JSON text in SQLite."""
    import json

    if not inspect(c).has_table("roles"):
        return []
    out = []
    for slug, name, caps, by in c.execute(text("SELECT slug, name, caps, created_by_id FROM roles")).fetchall():
        try:
            caps = json.loads(caps) if isinstance(caps, str) else (caps or [])
        except ValueError:
            caps = []
        out.append((slug, name, caps if isinstance(caps, list) else [], by))
    return out


def _r8_limited_ids(c: Connection) -> set[int]:
    """Accounts that are team-limited now: their role has the limit (a manager's does — also before the roles
    table was seeded), or the account carries it itself."""
    scoped = {slug for slug, _n, caps, _b in _r8_roles(c) if "team.scope" in caps} | {"manager"}
    ids = {uid for uid, role, lim in c.execute(text("SELECT id, role, team_limited FROM users WHERE is_axl = 0")).fetchall()
           if role in scoped or lim}
    return ids


def _r8_team_lead(c: Connection) -> None:
    """A role an admin built as a manager (the team limit and "Add people") keeps a manager's powers: it gets
    "Leads their team". Built-ins are re-synced from cbx/security/caps.py at every start, so only invented ones."""
    import json

    for slug, name, caps, _by in _r8_roles(c):
        if slug in ("owner", "admin", "manager", "user"):
            continue
        if "team.scope" in caps and "people.add" in caps and "team.lead" not in caps:
            c.execute(text("UPDATE roles SET caps = :c WHERE slug = :s"), {"c": json.dumps([*caps, "team.lead"]), "s": slug})
            _r8_audit(c, "role_changed", slug, f"{name}: + Leads their team — it was built as a team's manager (R8, 1.0.114)")


def _r8_team_limited_accounts(c: Connection) -> None:
    """D3: every account a team-limited person made is limited to the team as well. Repeated until nothing moves,
    so an account made by an account a manager made is caught too. Never owners, admins or the AI."""
    if not inspect(c).has_table("users"):
        return
    admins = {slug for slug, _n, caps, _b in _r8_roles(c) if {"admin.all", "everything"} & set(caps)} | {"owner", "admin"}
    for _ in range(10):
        limited = _r8_limited_ids(c)
        rows = [r for r in c.execute(text("SELECT u.id, u.email, u.role, b.email FROM users u JOIN users b ON b.id = u.created_by_id "
                                          "WHERE u.is_axl = 0 AND (u.team_limited IS NULL OR u.team_limited = 0)")).fetchall()
                if r[0] not in limited and r[2] not in admins and _creator_limited(c, r[3], limited)]
        if not rows:
            return
        for uid, email, role, by in rows:
            c.execute(text("UPDATE users SET team_limited = 1 WHERE id = :i"), {"i": uid})
            _r8_audit(c, "account_changed", email, f"limited to their team → yes — made by {by}, who is limited to a team (R8, 1.0.114; role {role})")


def _creator_limited(c: Connection, creator_email: str, limited: set[int]) -> bool:
    row = c.execute(text("SELECT id FROM users WHERE email = :e"), {"e": creator_email}).fetchone()
    return bool(row and row[0] in limited)


def _r8_roles_review(c: Connection) -> None:
    """D4: roles a team-limited person built that have powers but not the team limit are listed in the audit
    trail for the owner — not changed. Nobody a manager makes can hold them any more without the limit (R8)."""
    if not inspect(c).has_table("users"):
        return
    limited = _r8_limited_ids(c)
    for slug, name, caps, by in _r8_roles(c):
        if by in limited and "team.scope" not in caps and set(caps) - {"team.scope"}:
            people = c.execute(text("SELECT COUNT(*) FROM users WHERE role = :s AND is_axl = 0"), {"s": slug}).scalar() or 0
            _r8_audit(c, "role_review_needed", slug, f"'{name}' was built by a team-limited person (user #{by}) without "
                      f"'Their own team only'; {people} account(s) hold it. Review it on Roles (R8, 1.0.114).", outcome="pending")


def _axl_not_owner(c: Connection) -> None:
    insp = inspect(c)
    if not insp.has_table("users"):
        return
    rows = c.execute(text("SELECT id FROM users WHERE is_axl = 1 AND role = 'owner'")).fetchall()
    if not rows:
        return
    c.execute(text("UPDATE users SET role = 'admin' WHERE is_axl = 1 AND role = 'owner'"))
    if insp.has_table("audit_log"):
        for (uid,) in rows:
            c.execute(text("INSERT INTO audit_log (at, actor_id, actor_label, action, target, detail, outcome, meta, ip) "
                           "VALUES (CURRENT_TIMESTAMP, NULL, 'system', 'account_changed', :t, :d, 'ok', '{}', '')"),
                      {"t": f"user:{uid}", "d": "the AI's service account: role owner → admin (1.0.114, never-alone in code)"})


def _mfa_email_unproven(c: Connection) -> None:
    """1.0.113, once. An authenticator app was always proven by its first code, so those accounts are marked proven
    (when they were set up). "Use email codes instead" switched an account without sending or checking any code, so
    no email-codes account is proven: their next sign-in asks for an emailed code even on a browser they used before
    (cbx/api/auth.py login). Written to the audit trail with the count."""
    insp = inspect(c)
    if not insp.has_table("users") or "mfa_proven_at" not in {col["name"] for col in insp.get_columns("users")}:
        return
    c.execute(text("UPDATE users SET mfa_proven_at = COALESCE(mfa_enrolled_at, CURRENT_TIMESTAMP) WHERE mfa_method = 'totp' AND mfa_proven_at IS NULL"))
    n = c.execute(text("SELECT COUNT(*) FROM users WHERE mfa_method = 'email'")).scalar() or 0
    c.execute(text("UPDATE users SET mfa_proven_at = NULL WHERE mfa_method = 'email'"))
    if n and insp.has_table("audit_log"):
        from sqlalchemy.orm import Session

        from .audit import log

        s = Session(bind=c)
        try:
            log(s, "mfa_email_unproven", actor_label="system", target=f"{n} account(s)", outcome="blocked",
                detail=f"{n} account(s) on email codes were switched without a code ever being checked; "
                       "each one's next sign-in, from any browser, asks for an emailed code")
            s.flush()  # the runner commits the whole migration at once
        finally:
            s.close()


def _support_new_problem_undone(c: Connection) -> None:
    """1.0.111, once: cbx/support/inbox.undo_new_problems on this connection (its own savepoint: if it fails, nothing of it is kept
    and CBX starts as usual — the failure is logged)."""
    import logging

    if not inspect(c).has_table("support_mail") or not inspect(c).has_table("support_messages"):
        return
    from sqlalchemy.orm import Session

    sp = c.begin_nested()
    try:
        with Session(bind=c, autoflush=False, expire_on_commit=False) as s:
            from .support import inbox

            n = inbox.undo_new_problems(s)
            s.commit()  # the session's own savepoint inside `sp` (the migration's transaction commits it)
        sp.commit()
        if n:
            logging.getLogger("cbx.migrate").warning("support: %s mail(s) kept aside as a new problem added to their open tickets", n)
    except Exception:  # noqa: BLE001 — a repair never stops CBX starting
        sp.rollback()
        logging.getLogger("cbx.migrate").exception("support: the 1.0.111 new-problem repair failed")


def _hash_device_tokens(c: Connection) -> None:
    insp = inspect(c)
    if not insp.has_table("trusted_devices"):
        return
    from .security.devices import token_hash

    rows = c.execute(text("SELECT id, token FROM trusted_devices")).all()
    for did, tok in rows:
        if tok and not (len(tok) == 64 and all(ch in "0123456789abcdef" for ch in tok)):
            c.execute(text("UPDATE trusted_devices SET token=:t WHERE id=:i"), {"t": token_hash(tok), "i": did})


def _unique_grid_cells(c: Connection) -> None:
    insp = inspect(c)
    if not insp.has_table("watched_docs"):
        return
    # keep the cell that holds a file (or the newest) when a company/type pair exists twice
    dupes = c.execute(text("SELECT entity_id, doc_type_id FROM watched_docs WHERE doc_type_id IS NOT NULL GROUP BY entity_id, doc_type_id HAVING COUNT(*) > 1")).all()
    for eid, tid in dupes:
        rows = c.execute(text("SELECT id, file_id FROM watched_docs WHERE entity_id=:e AND doc_type_id=:t ORDER BY (file_id IS NULL), id DESC"), {"e": eid, "t": tid}).all()
        for rid, _ in rows[1:]:
            c.execute(text("DELETE FROM watched_docs WHERE id=:i"), {"i": rid})
    c.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_entity_doctype_cell ON watched_docs (entity_id, doc_type_id)"))


def _entities_responsible(c: Connection) -> None:
    from sqlalchemy.orm import Session

    insp = inspect(c)
    if not insp.has_table("entities"):
        return
    if "responsible" not in {col["name"] for col in insp.get_columns("entities")}:
        c.execute(text("ALTER TABLE entities ADD COLUMN responsible VARCHAR(80) DEFAULT ''"))
    s = Session(bind=c)
    try:
        for eid, notes in s.execute(text("SELECT id, notes FROM entities")).all():
            m = re.search(r"Responsible:\s*(.+)$", notes or "", re.IGNORECASE)
            if m:
                s.execute(text("UPDATE entities SET responsible=:r, notes=:n WHERE id=:i"), {"r": m.group(1).strip()[:80], "n": re.sub(r"\s*Responsible:\s*.+$", "", notes or "").strip(), "i": eid})
        s.flush()  # the runner commits the whole migration at once — a commit here would split it
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def _docwatch(c: Connection, step: str) -> None:
    """Run one Entity Compliance data step inside the migration's transaction (a Session bound to the
    connection flushes into it; the runner commits)."""
    from sqlalchemy.orm import Session

    from .docwatch import service

    insp = inspect(c)
    if not (insp.has_table("watched_docs") and insp.has_table("entities") and insp.has_table("doc_types")):
        return
    s = Session(bind=c)
    try:
        getattr(service, step)(s)
        s.flush()
    finally:
        s.close()


def _rebuild_if_notnull(c: Connection, table: str, column: str) -> None:
    """SQLite-only: if `table` exists and `column` is NOT NULL there but nullable in the model, recreate
    the table from the model and copy every row across (shared columns only). Postgres does ALTER."""
    from .db import Base

    insp = inspect(c)
    if not insp.has_table(table):
        return
    cols = {col["name"]: col for col in insp.get_columns(table)}
    if column not in cols or cols[column].get("nullable", True):
        return
    if c.engine.dialect.name != "sqlite":
        c.execute(text(f'ALTER TABLE {table} ALTER COLUMN "{column}" DROP NOT NULL'))
        return
    model = Base.metadata.tables[table]
    old = f"{table}__old"
    c.execute(text(f'ALTER TABLE {table} RENAME TO "{old}"'))
    model.create(c)
    shared = [k for k in cols if k in model.columns]
    # model columns the old table lacks and that must not be NULL get a sensible literal
    fill = []
    for mc in model.columns:
        if mc.name in shared or mc.nullable or mc.primary_key:
            continue
        d = _default_sql(mc).replace(" DEFAULT ", "", 1)
        if not d:
            t = str(mc.type).upper()
            d = "CURRENT_TIMESTAMP" if "DATE" in t or "TIME" in t else ("0" if any(x in t for x in ("INT", "BOOL", "FLOAT", "NUM")) else "''")
        fill.append((mc.name, d))
    names = ", ".join(f'"{k}"' for k in shared + [f for f, _ in fill])
    select = ", ".join([f'"{k}"' for k in shared] + [d for _, d in fill])
    c.execute(text(f'INSERT INTO {table} ({names}) SELECT {select} FROM "{old}"'))
    c.execute(text(f'DROP TABLE "{old}"'))


def _default_sql(col) -> str:
    """A literal DEFAULT for a column's simple Python default, or '' when there is none."""
    d = col.default
    if d is None or d.is_callable or d.is_sequence:
        return ""
    v = d.arg
    if isinstance(v, bool):
        return " DEFAULT 1" if v else " DEFAULT 0"
    if isinstance(v, (int, float)):
        return f" DEFAULT {v}"
    if isinstance(v, str):
        return " DEFAULT '" + v.replace("'", "''") + "'"
    return ""


def add_missing_columns(c: Connection) -> list[str]:
    """Safety net for installs older than the explicit list above: any column a model declares that
    the table lacks is added (nullable, with the model's simple default). Never drops or renames."""
    from .db import Base

    added = []
    insp = inspect(c)
    existing_tables = set(insp.get_table_names())
    for table in Base.metadata.tables.values():  # order is irrelevant for ADD COLUMN
        if table.name not in existing_tables:
            continue
        have = {col["name"] for col in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have or col.primary_key:
                continue
            ddl = col.type.compile(dialect=c.dialect)
            c.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ddl}{_default_sql(col)}'))
            added.append(f"{table.name}.{col.name}")
    return added


def run_migrations(engine: Engine) -> None:
    with engine.begin() as c:
        c.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (id TEXT PRIMARY KEY, applied_at TEXT)"))
        done = {r[0] for r in c.execute(text("SELECT id FROM schema_migrations"))}
        # columns first, so data migrations below can rely on every model column being there
        added = add_missing_columns(c)
        if added:
            import logging

            logging.getLogger("cbx.migrate").warning("added missing columns: %s", ", ".join(added))
        for mid, step in MIGRATIONS:
            if mid in done:
                continue
            if callable(step):
                step(c)
            else:
                c.execute(text(step))
            c.execute(text("INSERT INTO schema_migrations (id, applied_at) VALUES (:i, datetime('now'))"), {"i": mid})
