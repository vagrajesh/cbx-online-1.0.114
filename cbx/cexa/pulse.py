"""The pulse: C-EXA knows what is happening on this server at all times.

Every 60 s (`cexa-pulse`) a fixed list of checks runs; each writes one row in `pulse_state` (ok, a
short reading, a sentence). Two consecutive bad readings → one alert to the owners through the
watcher's channel (Home → From C-EXA, Talk to C-EXA, audit `pulse_alert`); when it clears, one recovery
note. Nothing is ever changed by the pulse — it only looks.

Checks:
  ollama        Ollama answers and C-EXA's model is loaded (live mode only)
  model_calls   failed model calls in the last hour (catches the timeout class of failure at once)
  batches       PDF batches sitting in learning/running/uploading for more than a day
  scheduler     the routine loop ticked in the last 10 minutes (if it dies, this is the only witness)
  disk          free space on the data drive
  database      the SQLite file's size and how fast it grows
  version       the VERSION file vs the code that is running (drift = restart pending or a hand edit)
  builds        a proposal building for more than 30 minutes

The table is also the snapshot behind `what_changed(since)`: which readings flipped, plus the audit
trail's counts since then. The morning brief says "Systems: all fine" or names what isn't.
"""
from __future__ import annotations

import logging
import shutil
import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import VERSION
from ..audit import log
from ..config import settings
from ..db import session_scope, utcnow
from ..models import AuditLog, CexaMessage, Batch, Fix, ModelCall, Proposal, PulseState, User
from .tools import tool

logger = logging.getLogger("cbx.cexa.pulse")

EVERY_SECONDS = 60
ALERT_AFTER = 2                 # consecutive bad readings before anyone is told
STUCK_BATCH_HOURS = 24
STUCK_BUILD_MINUTES = 30
SCHEDULER_STALE_MINUTES = 10
DISK_MIN_FREE_GB = 2.0
DISK_MIN_FREE_PCT = 5.0
MODEL_FAIL_RATIO = 0.5
MODEL_FAIL_MIN_CALLS = 3
DB_GROWTH_WARN_MB_PER_DAY = 500

Check = Callable[[Session], tuple[bool, str, str]]   # ok, short value, sentence
CHECKS: dict[str, Check] = {}
_started = time.time()


def check(name: str):
    def deco(fn: Check):
        CHECKS[name] = fn
        return fn

    return deco


# ---- the checks --------------------------------------------------------------------------------------


@check("ollama")
def _ollama(db: Session) -> tuple[bool, str, str]:
    from .gateway import ollama_status

    st = ollama_status()
    if st.get("mode") == "stub":
        return True, "stub", "C-EXA is in stub mode (fixed sentences) — Ollama is not used on this server."
    if not st.get("online"):
        return False, "offline", f"Ollama is not answering at {settings.ollama_url}. C-EXA is on fixed sentences and PDF Studio runs on rules only."
    if st.get("cexa_ready") is False:
        return False, "model missing", f"Ollama is up but {settings.cexa_model} is not pulled — C-EXA cannot think."
    advice = st.get("advice") or ""
    loaded = st.get("loaded") or []
    return (not advice), ("online · " + (", ".join(str(x.get("name", "")) for x in loaded[:2]) if loaded else "nothing loaded")), (advice or "Ollama is up and the model is placed.")


@check("model_calls")
def _model_calls(db: Session) -> tuple[bool, str, str]:
    since = utcnow() - timedelta(hours=1)
    rows = db.query(ModelCall.ok, func.count(ModelCall.id)).filter(ModelCall.at >= since).group_by(ModelCall.ok).all()
    n = sum(c for _, c in rows)
    bad = sum(c for ok, c in rows if not ok)
    if n < MODEL_FAIL_MIN_CALLS:
        return True, f"{n} call(s) in the last hour", "Too few model calls in the last hour to judge."
    ratio = bad / n
    return ratio < MODEL_FAIL_RATIO, f"{bad}/{n} failed", (f"{bad} of {n} model calls failed in the last hour — timeouts or a model that stopped answering." if ratio >= MODEL_FAIL_RATIO else f"{n} model calls in the last hour, {bad} failed.")


@check("batches")
def _batches(db: Session) -> tuple[bool, str, str]:
    cutoff = utcnow() - timedelta(hours=STUCK_BATCH_HOURS)
    stuck = db.query(Batch).filter(Batch.status.in_(["learning", "running", "uploading"]), Batch.created_at < cutoff).all()
    if not stuck:
        return True, "none stuck", "No PDF batch has been sitting in learning/running/uploading for more than a day."
    names = ", ".join(f"'{b.name}' ({b.status})" for b in stuck[:3]) + (f" and {len(stuck) - 3} more" if len(stuck) > 3 else "")
    return False, f"{len(stuck)} stuck", f"{len(stuck)} PDF batch{'es' if len(stuck) != 1 else ''} stuck for more than a day: {names}. Open PDF Studio and cancel or rerun them."


@check("scheduler")
def _scheduler(db: Session) -> tuple[bool, str, str]:
    from .. import jobs

    if jobs._inline:
        return True, "inline", "Jobs run inline (tests)."
    t = jobs.last_tick()
    if not t:
        age = time.time() - _started
        return age < SCHEDULER_STALE_MINUTES * 60, "not started", "The routine loop has not ticked yet since the server started."
    age_min = (time.time() - t) / 60
    return age_min < SCHEDULER_STALE_MINUTES, f"ticked {int(age_min)} min ago", (f"The routine loop last ran {int(age_min)} minutes ago — reminders, briefs and the watcher are not running. Restart run.bat." if age_min >= SCHEDULER_STALE_MINUTES else "Routines are running.")


@check("disk")
def _disk(db: Session) -> tuple[bool, str, str]:
    try:
        u = shutil.disk_usage(str(settings.data_dir))
    except OSError as e:
        return False, "unknown", f"Could not read disk space for {settings.data_dir}: {e}"
    free_gb = u.free / 1e9
    pct = 100.0 * u.free / max(1, u.total)
    ok = free_gb >= DISK_MIN_FREE_GB and pct >= DISK_MIN_FREE_PCT
    return ok, f"{free_gb:.1f} GB free ({pct:.0f}%)", (f"Only {free_gb:.1f} GB ({pct:.0f}%) free on the data drive — uploads and backups will start failing." if not ok else f"{free_gb:.1f} GB free on the data drive.")


@check("database")
def _database(db: Session) -> tuple[bool, str, str]:
    if settings.database_url:
        return True, "postgres", "The database is Postgres — size is its own business."
    f = settings.data_dir / "cbx.sqlite3"
    try:
        size_mb = f.stat().st_size / 1e6
    except OSError:
        return True, "no file", "No SQLite file found (fresh server)."
    prev = db.get(PulseState, "database")
    growth = ""
    ok = True
    if prev is not None and prev.value.endswith("MB") and prev.changed_at:
        try:
            prev_mb = float(prev.value.split(" ")[0])
            hours = max(1 / 60, (utcnow() - (prev.checked_at or utcnow())).total_seconds() / 3600)
            per_day = (size_mb - prev_mb) / hours * 24
            if per_day > DB_GROWTH_WARN_MB_PER_DAY:
                ok = False
                growth = f" and growing about {per_day:.0f} MB a day — something is writing far more than usual."
        except ValueError:
            pass
    return ok, f"{size_mb:.1f} MB", f"The database is {size_mb:.1f} MB{growth}."


@check("version")
def _version(db: Session) -> tuple[bool, str, str]:
    from ..versions.injector import current_tag

    tag = current_tag()
    if tag == VERSION:
        return True, VERSION, f"Running {VERSION}; the VERSION file agrees."
    restart = (settings.data_dir / "RESTART_REQUESTED").exists()
    return False, f"{VERSION} running, file says {tag}", (f"A promote to {tag} is waiting for the restart (RESTART_REQUESTED is set) — the server is still on {VERSION}." if restart else f"The VERSION file says {tag} but the running code is {VERSION}: files were changed outside the Versions page. Promote properly, or restart.")


@check("builds")
def _builds(db: Session) -> tuple[bool, str, str]:
    cutoff = utcnow() - timedelta(minutes=STUCK_BUILD_MINUTES)
    stuck = db.query(Proposal).filter(Proposal.status == "building", Proposal.updated_at < cutoff).all()
    if not stuck:
        return True, "none", "No sandbox build has been running for more than 30 minutes."
    return False, f"{len(stuck)} stuck", f"{len(stuck)} sandbox build(s) running for more than 30 minutes: " + ", ".join(f"#{p.id} '{p.title}'" for p in stuck[:3]) + ". The board routine hands them back to Noticed."


@check("mail")
def _mail(db: Session) -> tuple[bool, str, str]:
    """Is the mailbox working? Sign-in codes fall open when a code cannot be sent (by design — a door
    nobody can open is not security), so the failure has to be LOUD instead: here, within two minutes,
    to the owners. Off or unconfigured is not a failure; failing to deliver once it is on is."""
    from .. import mail
    from ..models import MailLog

    ready, why = mail.configured()
    if not ready:
        return True, "off", f"Mail is off ({why.rstrip('.')}) — nothing to watch."
    since = utcnow() - timedelta(hours=1)
    rows = db.query(MailLog).filter(MailLog.created_at >= since).order_by(MailLog.id.desc()).limit(50).all()
    # a refused address, or a mail held as a draft to check first, is a rule working, not a failure
    rows = [r for r in rows if not (r.detail or "").startswith(("refused:", "draft:"))]
    failed = [r for r in rows if not r.ok]
    if rows and not rows[0].ok:  # the newest attempt failed — and nothing has gone out since
        last = rows[0]
        return False, f"{len(failed)} failed", f"Mail is not going out: {len(failed)} message(s) failed in the last hour, last '{last.subject[:60]}' — {last.detail[:120]}. Sign-in codes are being skipped while this lasts."
    return True, f"{sum(1 for r in rows if r.ok)} sent/h", "Mail is going out."


# ---- one pass ----------------------------------------------------------------------------------------


def _owners(db: Session) -> list[User]:
    return db.query(User).filter(User.role == "owner", User.is_axl.is_(False), User.is_active.is_(True)).all()


def _cexa(db: Session) -> Optional[User]:
    return db.query(User).filter(User.is_axl.is_(True)).one_or_none()


def _say(db: Session, title: str, text: str, *, action: str, outcome: str) -> None:
    for o in _owners(db):
        db.add(CexaMessage(user_id=o.id, from_axl=True, kind="watch", title=title, text=text, link="#/cexa", meta={"agent": "pulse"}))
    log(db, action, actor=_cexa(db), actor_label="axl→pulse", target=title, detail=text[:500], outcome=outcome)


def run(db: Session, now: Optional[datetime] = None, only: Optional[list[str]] = None) -> dict:
    """One pass over every check. Returns {key: {ok, value, alerted, recovered}}."""
    now = now or utcnow()
    out: dict = {}
    for key, fn in CHECKS.items():
        if only and key not in only:
            continue
        try:
            ok, value, detail = fn(db)
        except Exception as e:  # noqa: BLE001 — one broken check must not stop the others
            logger.warning("pulse check %s crashed: %s", key, e)
            ok, value, detail = False, "check failed", f"The {key} check itself failed: {type(e).__name__}: {str(e)[:120]}"
        row = db.get(PulseState, key)
        if row is None:
            row = PulseState(key=key, ok=ok, value=value[:200], detail=detail, bad_streak=0, alerted=False, checked_at=now, changed_at=now)
            db.add(row)
        was_ok = row.ok
        alerted = recovered = False
        if ok:
            if not was_ok and row.alerted:
                _say(db, f"{key} is back", f"{detail} (It had been wrong since {row.changed_at:%H:%M} UTC.)", action="pulse_recovered", outcome="ok")
                recovered = True
            row.bad_streak = 0
            row.alerted = False
        else:
            row.bad_streak = (row.bad_streak or 0) + 1
            if row.bad_streak >= ALERT_AFTER and not row.alerted:
                _say(db, f"Pulse: {key}", detail, action="pulse_alert", outcome="blocked")
                row.alerted = True
                alerted = True
        if ok != was_ok:
            row.changed_at = now
        row.ok, row.value, row.detail, row.checked_at = ok, value[:200], detail, now
        db.add(row)
        out[key] = {"ok": ok, "value": value, "alerted": alerted, "recovered": recovered}
    db.flush()
    return out


def snapshot(db: Session) -> list[dict]:
    rows = db.query(PulseState).order_by(PulseState.key).all()
    return [{"key": r.key, "ok": r.ok, "value": r.value, "detail": r.detail, "checked_at": r.checked_at.isoformat() if r.checked_at else None, "changed_at": r.changed_at.isoformat() if r.changed_at else None, "bad_streak": r.bad_streak} for r in rows]


def systems_line(db: Session) -> str:
    """One line for the morning brief."""
    rows = db.query(PulseState).all()
    if not rows:
        return "Systems: no pulse yet."
    bad = [r for r in rows if not r.ok]
    if not bad:
        return "Systems: all fine — " + ", ".join(f"{r.key} {r.value}" for r in sorted(rows, key=lambda x: x.key) if r.key in ("ollama", "disk", "version"))[:160] + "."
    return "Systems: " + "; ".join(f"{r.key} — {r.detail}" for r in bad)[:400]


# ---- the routine -------------------------------------------------------------------------------------


from ..jobs import routine  # noqa: E402


@routine("cexa-pulse", EVERY_SECONDS)
def pulse_routine() -> None:
    from sqlalchemy.exc import OperationalError

    try:
        with session_scope() as db:
            r = run(db)
    except OperationalError as e:
        # SQLite has one writer: while a version's tests run or a batch is being written, a heartbeat
        # that cannot get the lock waits for the next minute rather than filling the log with a trace.
        if "locked" in str(e).lower():
            logger.info("pulse: database busy — skipped this tick")
            return
        raise
    bad = [k for k, v in r.items() if not v["ok"]]
    if any(v["alerted"] or v["recovered"] for v in r.values()):
        logger.info("pulse: %s", ", ".join(f"{k}={'ok' if v['ok'] else 'BAD'}" for k, v in r.items()))
    elif bad:
        logger.debug("pulse: bad %s", bad)


# ---- tools -------------------------------------------------------------------------------------------


@tool("pulse", "read", "How the server is right now: Ollama, model calls, stuck batches, the routine loop, disk, database size, version drift, sandbox builds — from the last minute's pulse.", {})
def pulse_tool(db: Session, me: User, fix: Optional[Fix], **_) -> str:
    if not me.is_admin:
        return "Only admins and owners can read the pulse."
    rows = snapshot(db)
    if not rows:
        run(db)
        rows = snapshot(db)
    lines = [f"{'OK ' if r['ok'] else 'BAD'} {r['key']:<12} {r['value']}" + ("" if r["ok"] else f" — {r['detail']}") for r in rows]
    newest = max((r["checked_at"] or "" for r in rows), default="")
    return "\n".join(lines) + (f"\n(checked {newest[11:16]} UTC)" if newest else "")


@tool("what_changed", "read", "What changed on the server since a time: which pulse readings flipped, and what the audit trail recorded (sign-ins, filings, promotions, errors), counted.", {"since": "e.g. 'yesterday', '24h', '3d', or a date 2026-09-20"})
def what_changed(db: Session, me: User, fix: Optional[Fix], since: str = "24h", **_) -> str:
    if not me.is_admin:
        return "Only admins and owners can ask that."
    now = utcnow()
    s = (since or "24h").strip().lower()
    start: Optional[datetime] = None
    if s in ("yesterday", "1d", "24h", "day"):
        start = now - timedelta(days=1)
    elif s.endswith("h") and s[:-1].isdigit():
        start = now - timedelta(hours=int(s[:-1]))
    elif s.endswith("d") and s[:-1].isdigit():
        start = now - timedelta(days=int(s[:-1]))
    elif s == "week":
        start = now - timedelta(days=7)
    else:
        try:
            start = datetime.fromisoformat(s).replace(tzinfo=now.tzinfo)
        except ValueError:
            start = now - timedelta(days=1)
    flips = [r for r in db.query(PulseState).filter(PulseState.changed_at >= start).all()]
    rows = db.query(AuditLog.action, AuditLog.outcome, func.count(AuditLog.id)).filter(AuditLog.at >= start).group_by(AuditLog.action, AuditLog.outcome).all()
    counts: Counter = Counter()
    trouble: Counter = Counter()
    for action, outcome, n in rows:
        counts[action] += n
        if outcome not in ("ok",):
            trouble[f"{action} ({outcome})"] += n
    people = db.query(func.count(func.distinct(AuditLog.actor_label))).filter(AuditLog.at >= start, AuditLog.action == "login").scalar() or 0
    top = ", ".join(f"{a} ×{n}" for a, n in counts.most_common(12))
    out = [f"Since {start:%d %b %H:%M} UTC:"]
    out.append(f"- Systems: " + ("; ".join(f"{r.key} → {'ok' if r.ok else 'BAD'} ({r.value})" for r in flips) if flips else "no reading changed."))
    out.append(f"- People: {counts.get('login', 0)} sign-in(s) by {people} person/people; {counts.get('login_failed', 0)} failed.")
    out.append(f"- Work: {counts.get('docwatch_filed', 0) + counts.get('docwatch_renewed', 0)} certificate(s) filed/renewed, {counts.get('pdf_batch_finished', 0)} PDF batch(es) finished, {counts.get('workflow_run', 0)} tool run(s), {counts.get('mail_sent', 0)} mail(s) sent.")
    out.append(f"- Code: {counts.get('version_promoted', 0)} promotion(s), {counts.get('proposal_built', 0)} build(s), {counts.get('cexa_hands', 0)} sandbox action(s) by me.")
    out.append("- Trouble: " + (", ".join(f"{k} ×{n}" for k, n in trouble.most_common(8)) if trouble else "none recorded."))
    out.append(f"- Everything, by action: {top or 'nothing recorded'}.")
    return "\n".join(out)
