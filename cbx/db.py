from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import DateTime, TypeDecorator, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from starlette.requests import Request

from .config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Always aware UTC datetimes, on every database (SQLite drops tzinfo)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    pass


def _make_engine(url: str):
    if url.startswith("sqlite"):
        eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            # Two writers at once is normal here: the browser fires several calls in parallel and
            # the background routines write on their own thread. Without these, SQLite gives up on a
            # busy database almost at once and the person sees a spinner that never resolves.
            # busy_timeout makes a blocked writer wait instead of failing; synchronous=NORMAL is the
            # documented pairing for WAL and takes the fsync out of every commit.
            # This reduces the window; it does not remove it — SQLite still has one writer at a time.
            # Postgres (CBX_DATABASE_URL) is the real answer once more than a handful of people are on.
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        return eng
    return create_engine(url, pool_pre_ping=True)


engine = _make_engine(settings.db_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(SessionLocal, "after_flush")
def _remember_audit_rows(session: Session, _ctx) -> None:
    """Keep a handle on every audit row written in this session (see `_rollback_keeping_audit`)."""
    from .models import AuditLog

    rows = session.info.setdefault("audit_rows", [])
    rows.extend(o for o in session.new if isinstance(o, AuditLog))


@event.listens_for(SessionLocal, "after_commit")
def _forget_committed_audit_rows(session: Session) -> None:
    """Once committed, an audit row is safe — and must never be written again.

    Without this, any endpoint that commits for itself and then raises (sign-in does exactly that,
    to keep the failed-attempt counter before returning 401) got its audit rows written TWICE: the
    commit stored them, then `_rollback_keeping_audit` found them still on the list and re-inserted
    the snapshot. Same row, same timestamp to the microsecond, two ids. Every count built on the
    audit trail was double — including the overnight briefing C-EXA sends, which is how "12 failed
    sign-ins" reached Edwin as 24."""
    session.info["audit_rows"] = []
    # Work that must wait for the commit (see `after_commit`). Run here, once, then forgotten.
    todo, session.info["after_commit"] = list(session.info.get("after_commit") or []), []
    for fn, args, kwargs in todo:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 — a follow-up job must never undo a committed request
            import logging

            logging.getLogger("cbx.db").exception("after_commit job %s failed", getattr(fn, "__name__", fn))


def after_commit(session: Session, fn, *args, **kwargs) -> None:
    """Run `fn` once this session's current transaction has committed — not before.

    For anything that opens its own database connection (a background job answering in chat, say):
    started inside the transaction it would wait on the very lock the request is holding, and under
    the test runner, which runs jobs inline on the same thread, that is a deadlock until busy_timeout
    gives up. After the commit there is nothing to wait for."""
    session.info.setdefault("after_commit", []).append((fn, args, kwargs))


def _rollback_keeping_audit(s: Session) -> None:
    """A request that ends in an error (403 denied, 400 refused, VersionError…) is rolled back, but the
    audit trail must still say it happened: `log(db, "…_blocked", outcome="blocked")` is called right
    before the HTTPException is raised, and a plain rollback would silently drop that row. So: snapshot
    the audit rows written in this session, roll everything else back, and write them again on their own."""
    from .models import AuditLog

    snap: list[dict] = []
    for o in s.info.get("audit_rows") or []:
        try:
            snap.append({c.name: getattr(o, c.name) for c in AuditLog.__table__.columns if c.name != "id"})
        except Exception:  # noqa: BLE001 — never let bookkeeping mask the real error
            pass
    s.rollback()
    s.info["audit_rows"] = []
    if snap:
        try:
            s.add_all(AuditLog(**d) for d in snap)
            s.commit()
        except Exception:  # noqa: BLE001
            s.rollback()


def init_db() -> None:
    # Import models so they register on Base.metadata.
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)
    from .migrate import run_migrations

    run_migrations(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        _rollback_keeping_audit(s)
        raise
    finally:
        s.close()


def get_db(request: Request) -> Iterator[Session]:
    """One session per request. The session is also parked on `request.state.db` so the shell's middleware
    (cbx/main.py: `commit_before_response`) can commit it BEFORE the response goes out — FastAPI 0.118+ runs
    this generator's exit code only after the response has been sent, which let a browser race its own
    write (sign in → /me 401). The commit below is then a no-op; the error path is unchanged."""
    s = SessionLocal()
    request.state.db = s
    try:
        yield s
        s.commit()
    except Exception:
        _rollback_keeping_audit(s)
        raise
    finally:
        s.close()
