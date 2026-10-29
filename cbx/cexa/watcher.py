"""The watcher: C-EXA's first standing agent. Reads, never writes anything but its own notes.

Edwin's brief, 18 Sep: an agent "doing logins, tracking, reporting — no need to approve but keep me
up to date". So this one:

* reads every new audit row, every five minutes, and never anything else — it does not touch a
  document, a batch, a person or a setting;
* speaks up at once when something looks wrong: a burst of failed sign-ins for one account, a
  person being refused over and over (probing), a server error, a routine that died, sign-ins going
  through unverified because email is not proven yet;
* once a day, first thing, writes the owner one plain paragraph on the day before: who signed in,
  what failed, what was filed and read;
* everything it says goes to the owners' C-EXA inbox (Home → From C-EXA, and Talk to C-EXA), is written
  to the audit trail as `watcher_alert` / `watcher_report`, and appears on the Agents page as its
  notes. Nothing needs approving because nothing is changed.

It keeps its place with one number — the last audit row it read — in `app_state`, so a restart
neither repeats an alert nor skips one.
"""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from .. import state
from ..audit import log
from ..db import session_scope, utcnow
from ..jobs import routine
from ..models import AuditLog, CexaMessage, User
from . import agents

logger = logging.getLogger("cbx.cexa.watcher")

CURSOR = "watcher.cursor"          # id of the last audit row read
TALLY = "watcher.tally"            # today's running counts, as JSON
REPORTED = "watcher.reported_on"   # ISO date of the last daily report
UNVERIFIED_ON = "watcher.unverified_on"  # date the "codes are off" alert last went out

EVERY_SECONDS = 300
REPORT_HOUR_LOCAL = 7              # Africa/Johannesburg, UTC+2
LOCAL_OFFSET = timedelta(hours=2)
BURST = 5                          # failed sign-ins for one account within one run
PROBING = 5                        # refusals for one person within one run
BATCH = 5000                       # most rows read in one run; the rest next time

ERROR_ACTIONS = ("server_error", "routine_failed", "job_failed")
REFUSAL_ACTIONS = ("tool_denied", "docwatch_denied", "role_edit_blocked")


# ---- helpers ---------------------------------------------------------------------------------------

def _owners(db: Session) -> list[User]:
    return db.query(User).filter(User.role == "owner", User.is_axl.is_(False), User.is_active.is_(True)).all()


def _cexa(db: Session) -> Optional[User]:
    return db.query(User).filter(User.is_axl.is_(True)).one_or_none()


def _say(db: Session, text: str, *, kind: str, title: str, link: str = "#/audit", action: str = "watcher_alert") -> int:
    """One note to every owner, once in the audit trail."""
    n = 0
    for o in _owners(db):
        db.add(CexaMessage(user_id=o.id, from_axl=True, kind=kind, title=title, text=text, link=link, meta={"agent": "watcher"}))
        n += 1
    log(db, action, actor=_cexa(db), actor_label="axl→watcher", target=title, detail=text[:500], outcome="ok" if action == "watcher_report" else "blocked")
    return n


def _tally(db: Session) -> dict:
    raw = state.get(db, TALLY)
    try:
        t = json.loads(raw) if raw else {}
    except ValueError:
        t = {}
    today = (utcnow() + LOCAL_OFFSET).date().isoformat()
    if t.get("day") != today:
        t = {"day": today, "prev": t if t.get("day") else None}
    for k in ("logins", "failed", "unverified", "blocked", "errors", "filed", "batches", "rows", "trusted", "roles"):
        t.setdefault(k, 0)
    t.setdefault("people", [])
    return t


def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


# ---- the run ---------------------------------------------------------------------------------------

def run(db: Session, now: Optional[datetime] = None) -> dict:
    """One pass: read what is new, alert on what is wrong, and at the right hour write the report."""
    now = now or utcnow()
    cursor = int(state.get(db, CURSOR) or 0)
    rows = db.query(AuditLog).filter(AuditLog.id > cursor).order_by(AuditLog.id.asc()).limit(BATCH).all()
    out = {"read": len(rows), "alerts": 0, "report": False}
    t = _tally(db)

    if rows:
        failed: Counter = Counter()
        failed_ip: dict[str, set] = defaultdict(set)
        refused: Counter = Counter()
        errors: list[AuditLog] = []
        unverified = 0
        for a in rows:
            act = a.action
            if a.actor_label == "axl→watcher":
                continue  # its own earlier notes
            if act == "login":
                t["logins"] += 1
                if a.actor_label and a.actor_label not in t["people"]:
                    t["people"].append(a.actor_label)
            elif act == "login_failed":
                t["failed"] += 1
                failed[a.actor_label or "?"] += 1
                if a.ip:
                    failed_ip[a.actor_label or "?"].add(a.ip)
            elif act == "login_unverified":
                t["unverified"] += 1
                unverified += 1
            elif act == "device_trusted":
                t["trusted"] += 1
            elif act in ERROR_ACTIONS:
                t["errors"] += 1
                errors.append(a)
            elif a.outcome == "blocked" and act in REFUSAL_ACTIONS:
                t["blocked"] += 1
                refused[a.actor_label or "?"] += 1
            elif act in ("docwatch_filed", "docwatch_renewed", "docwatch_added"):
                t["filed"] += 1
            elif act == "pdf_batch_finished":
                t["batches"] += 1
            elif act in ("role_created", "role_changed", "role_deleted"):
                t["roles"] += 1
            t["rows"] += 1

        # -- what needs saying now ------------------------------------------------------------
        for who, n in failed.items():
            if n >= BURST:
                ips = ", ".join(sorted(failed_ip[who])[:3])
                out["alerts"] += _say(db, f"{n} failed sign-ins for {who} in the last few minutes" + (f", from {ips}" if ips else "") + ". The account locks itself after 5 and unlocks in 15 minutes; if this is not them, change their password from Users & roles.",
                                      kind="watch", title=f"Sign-in attempts on {who}", link="#/audit?action=login_failed")
        for who, n in refused.items():
            if n >= PROBING:
                out["alerts"] += _say(db, f"{who} was refused {n} times in a few minutes — pages or tools they do not have. Usually a person clicking around; worth a glance if it keeps happening.",
                                      kind="watch", title=f"{who} kept hitting locked doors", link="#/audit")
        if errors:
            first = errors[0]
            more = f" and {len(errors) - 1} more" if len(errors) > 1 else ""
            out["alerts"] += _say(db, f"{_plural(len(errors), 'error')} in the app: {first.action.replace('_', ' ')} on {first.target}{more}. Detail is in the audit trail under {first.action}.",
                                  kind="watch", title="The app reported an error", link=f"#/audit?action={first.action}")
        if unverified and state.get(db, UNVERIFIED_ON) != t["day"]:
            state.set(db, UNVERIFIED_ON, t["day"])
            out["alerts"] += _say(db, f"{_plural(unverified, 'sign-in')} went through without a code today: sign-in codes are off until one test email has actually been delivered (Settings → Email → Send test). Not urgent, but it is the door I would close first.",
                                  kind="watch", title="Sign-in codes are still off", link="#/settings")
        # Move past what was read AND past the alert rows just written, so the watcher never reads
        # its own notes back as news.
        db.flush()
        from sqlalchemy import func

        newest = db.query(func.max(AuditLog.id)).scalar() or rows[-1].id
        state.set(db, CURSOR, str(max(newest, rows[-1].id)))

    # -- the daily report --------------------------------------------------------------------------
    local = now + LOCAL_OFFSET
    if local.hour >= REPORT_HOUR_LOCAL and state.get(db, REPORTED) != local.date().isoformat():
        prev = t.get("prev")
        if prev and prev.get("rows"):
            out["report"] = True
            _say(db, _report_text(prev), kind="brief", title=f"Watcher — {prev['day']}", link="#/audit", action="watcher_report")
        state.set(db, REPORTED, local.date().isoformat())

    state.set(db, TALLY, json.dumps(t))
    return out


def _report_text(p: dict) -> str:
    people = p.get("people") or []
    bits = [f"{_plural(p.get('logins', 0), 'sign-in')} by {_plural(len(people), 'person', 'people')}"]
    if p.get("failed"):
        bits.append(f"{_plural(p['failed'], 'failed attempt')}")
    if p.get("unverified"):
        bits.append(f"{p['unverified']} without a code")
    if p.get("trusted"):
        bits.append(f"{_plural(p['trusted'], 'new device')} confirmed")
    line1 = ", ".join(bits) + "."
    work = []
    if p.get("filed"):
        work.append(f"{_plural(p['filed'], 'certificate')} filed or renewed")
    if p.get("batches"):
        work.append(f"{_plural(p['batches'], 'PDF batch', 'PDF batches')} finished")
    if p.get("roles"):
        work.append(f"{_plural(p['roles'], 'role change')}")
    line2 = (", ".join(work) + ".") if work else "No certificates filed and no batches finished."
    line2 = line2[0].upper() + line2[1:]
    trouble = []
    if p.get("errors"):
        trouble.append(f"{_plural(p['errors'], 'error')} in the app")
    if p.get("blocked"):
        trouble.append(f"{_plural(p['blocked'], 'refusal')} (pages or tools people do not have)")
    line3 = ("Trouble: " + " and ".join(trouble) + " — all in the audit trail.") if trouble else "Nothing went wrong that I saw."
    return f"{line1} {line2} {line3} ({_plural(p.get('rows', 0), 'audit entry', 'audit entries')} read.)"


# ---- the routine -----------------------------------------------------------------------------------

@routine("cexa-watcher", EVERY_SECONDS)
def watch() -> None:
    with agents.working("watcher", task="Reading the audit trail", ref="audit") as a:
        with session_scope() as db:
            r = run(db)
        a.progress(1.0)
        note = f"Read {r['read']} new entr{'y' if r['read'] == 1 else 'ies'}" + (f" · {r['alerts']} alert{'s' if r['alerts'] != 1 else ''}" if r["alerts"] else " · nothing unusual") + (" · daily report sent" if r["report"] else "")
        a.observe(note)
        if r["alerts"] or r["report"]:
            logger.info("watcher: %s", note)
