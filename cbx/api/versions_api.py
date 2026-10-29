"""Versions & sandboxes: upload a version zip, see its tests, promote or roll back. Owner only, and
promote/rollback need a person (C-EXA is refused)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import VERSION
from ..audit import log
from ..db import get_db
from ..util import read_capped
from ..models import AppVersion, User
from ..security.deps import require_human, require_owner
from ..versions import injector, rollout, sandbox_runner
from ..versions.injector import VersionError, version_out

router = APIRouter(prefix="/api/versions", tags=["versions"])


@router.get("")
@router.get("/")
def list_versions(db: Session = Depends(get_db), me: User = Depends(require_owner)):
    rows = db.query(AppVersion).order_by(AppVersion.created_at.desc()).all()
    return {"versions": [version_out(v, db) for v in rows], "current": injector.current_tag(), "code_version": VERSION, "sandboxes": injector.sandboxes(db), "runtime": _runtime(), "pending": rollout.pending()}


def _runtime() -> list[dict]:
    """What is running in each slot — with its sign-ins, because only the owner reads this page."""
    out = []
    for r in sandbox_runner.status():
        r = dict(r)
        r["door"] = f"/sandbox/{r['slot']}/enter"
        r["login"] = [{"email": l["email"], "password": l["password"]} for l in sandbox_runner.logins(r["slot"])] if r.get("running") else []
        out.append(r)
    return out


def sandbox_owners(db: Session) -> list[tuple[str, str]]:
    """The live owners who get a sign-in of their own in a sandbox (people only; C-EXA never signs in)."""
    rows = db.query(User).filter(User.role == "owner", User.is_axl.is_(False), User.is_active.is_(True)).order_by(User.id).all()
    return [(u.email, u.name) for u in rows]


@router.get("/sandboxes")
def sandboxes(db: Session = Depends(get_db), me: User = Depends(require_owner)):
    return {"sandboxes": injector.sandboxes(db), "runtime": _runtime()}


@router.post("/sandboxes/{slot}/open")
def sandbox_open(slot: str, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    """Run the staged package of this slot so the owner can click through it before promoting.
    The sandbox uses its own throwaway data dir — the live data is untouchable."""
    if slot not in injector.SANDBOX_SLOTS:
        raise HTTPException(404, "No such sandbox slot.")
    occupant = next((s for s in injector.sandboxes(db) if s["slot"] == slot), None)
    if not occupant or not occupant.get("occupant"):
        raise HTTPException(400, f"{slot} is empty — stage (upload or let C-EXA build) something first.")
    path = occupant["occupant"].get("path") or ""
    if not path or not Path(path).exists():
        raise HTTPException(400, "The staged package is missing on disk — retest or upload it again.")
    occ = occupant["occupant"]
    out = sandbox_runner.start(slot, path, owners=sandbox_owners(db), kind=occ.get("kind") or "version", title=(("v" if occ.get("kind") == "version" else "") + str(occ.get("title") or "")))
    from ..audit import log

    log(db, "sandbox_opened", actor=me, target=slot, detail=f"{occupant['occupant'].get('title', '')} → {out.get('url', '')}; sign-ins for {', '.join(l['email'] for l in out.get('login', [])) or 'nobody'}", outcome="ok" if out.get("ok") else "failed")
    return out


@router.post("/sandboxes/{slot}/playground")
def sandbox_playground(slot: str, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    """A sandbox with nothing to review: the live app copied and started on this slot's port with its own
    empty database and the owner's sandbox sign-in — to look around, try things, show someone. Only a
    free slot; a build or a staged version in it is never pushed aside."""
    if slot not in injector.SANDBOX_SLOTS:
        raise HTTPException(404, "No such sandbox slot.")
    occupant = next((s for s in injector.sandboxes(db) if s["slot"] == slot), None)
    if occupant and occupant.get("occupant"):
        raise HTTPException(409, f"{slot} holds {occupant['occupant'].get('title', 'a build')} — stop or promote that first, or use the other slot.")
    from ..cexa import developer

    dest = developer.make_playground(slot)
    out = sandbox_runner.start(slot, str(dest), owners=sandbox_owners(db), kind="playground", title=f"Playground · copy of v{VERSION}")
    from ..audit import log

    log(db, "playground_opened", actor=me, target=slot, detail=f"copy of v{VERSION} → {out.get('url', '')}; sign-ins for {', '.join(l['email'] for l in out.get('login', [])) or 'nobody'}", outcome="ok" if out.get("ok") else "failed")
    return out


@router.post("/sandboxes/{slot}/stop")
def sandbox_stop(slot: str, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    if slot not in injector.SANDBOX_SLOTS:
        raise HTTPException(404, "No such sandbox slot.")
    out = sandbox_runner.stop(slot)
    from ..audit import log

    log(db, "sandbox_stopped", actor=me, target=slot)
    return out


@router.post("/upload")
async def upload(files: list[UploadFile] = File(...), notes: str = Form(""), db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    if not files:
        raise HTTPException(400, "Choose a zip file first.")
    f = files[0]
    data = await read_capped(f, injector.MAX_ZIP, "zip")
    try:
        v = injector.stage(db, data, me, notes)
    except VersionError as e:
        raise HTTPException(400, str(e))
    return version_out(v, db)


@router.get("/{vid}/diff")
def diff(vid: int, db: Session = Depends(get_db), me: User = Depends(require_owner)):
    v = db.get(AppVersion, vid)
    if not v:
        raise HTTPException(404, "That version doesn't exist.")
    return {"diff": injector.diff_against_live(v)}


@router.post("/{vid}/retest")
def retest(vid: int, db: Session = Depends(get_db), me: User = Depends(require_owner)):
    v = db.get(AppVersion, vid)
    if not v:
        raise HTTPException(404, "That version doesn't exist.")
    if v.status not in ("staged", "sandbox", "failed"):
        raise HTTPException(400, f"Version {v.tag} is {v.status}; only staged versions can be re-tested.")
    return version_out(injector.test_version(db, v), db)


@router.post("/pending/cancel")
def cancel_pending(db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    """Call off an announced update before it switches. Nothing has changed on disk, so nothing to undo."""
    return rollout.cancel(db, me)


@router.post("/{vid}/promote")
def promote(vid: int, force: bool = False, override_council: bool = False, skip_test: bool = False, minutes: int = rollout.DEFAULT_MINUTES, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    """`minutes` (default 10) is the notice people get: the version is announced, every open page shows
    the countdown and saves what is being typed, and the switch (backup, copy, pip, restart) runs when
    the timer ends. `minutes=0` switches now — for a security fix, or an empty office.

    `?force=1` promotes a version whose tests failed — the page shows the failing tests first and
    the audit row says it was forced. There so a test that is wrong for this server can't lock the
    owner out of updating; not a shortcut for C-EXA (refused) or for anyone but the owner."""
    v = db.get(AppVersion, vid)
    if not v:
        raise HTTPException(404, "That version doesn't exist.")
    if not (v.tests or {}).get("in_test") and not skip_test:
        # 1.0.80: Dev → Test → Live. Straight to Live only on purpose (a security fix, nobody to test).
        raise HTTPException(409, f"test: v{v.tag} has not been in Test. Put it in Test first (Work areas), or promote it without Test on purpose.")
    if not (v.tests or {}).get("in_test"):
        log(db, "version_promoted_without_test", actor=me, target=v.tag)
    council = (v.tests or {}).get("council") or {}
    if council.get("verdict") == "block" and not override_council:
        # C-EXA's Governance council blocked it: the owner may still promote, but only on purpose
        raise HTTPException(409, f"council: {council.get('summary') or 'the council blocked this version'} Open the council's review (job {council.get('job')}) before you decide.")
    if council.get("verdict") == "block":
        log(db, "version_council_overridden", actor=me, target=v.tag, detail=council.get("summary", "")[:200], outcome="ok")
    try:
        if minutes > 0:
            p = rollout.announce(db, v, me, minutes=minutes, force=bool(force))
            return {**version_out(v, db), "announced": p, "restart": False}
        cur = rollout.pending()
        if cur:
            raise VersionError(f"Version {cur['tag']} is announced for {cur['switch_at'][11:16]} UTC. Cancel that first.")
        injector.promote(db, v, me, force=bool(force))
    except VersionError as e:
        raise HTTPException(400, str(e))
    return {**version_out(v, db), "restart": injector.RESTART_ENABLED}


@router.delete("/{vid}")
def delete_version(vid: int, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    v = db.get(AppVersion, vid)
    if not v:
        raise HTTPException(404, "That version doesn't exist.")
    # a sandbox instance may be running out of this package: stop it first so its files can go
    for s in injector.sandboxes(db):
        occ = s.get("occupant") or {}
        if occ.get("kind") == "version" and occ.get("id") == v.id:
            sandbox_runner.stop(s["slot"])
    from ..models import WorkArea

    for a in db.query(WorkArea).filter(WorkArea.version_id == v.id).all():  # 1.0.80: in Test — out of it first
        sandbox_runner.stop(a.slot)
        a.version_id = None
    db.flush()
    try:
        return injector.delete(db, v, me)
    except VersionError as e:
        raise HTTPException(400, str(e))


@router.post("/{vid}/rollback")
def rollback(vid: int, data: bool = False, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    """1.0.114: `?data=1` also puts the database back as it was just before this version was promoted."""
    v = db.get(AppVersion, vid)
    if not v:
        raise HTTPException(404, "That version doesn't exist.")
    try:
        if data:
            # 1.0.114: a plain answer — after the data is put back nothing here may touch the database through the models
            # (they may expect columns the restored tables don't have); the server restarts on the old code anyway
            return {**injector.rollback_data(db, v, me), "restart": injector.RESTART_ENABLED}
        v = injector.rollback(db, v, me)
    except VersionError as e:
        raise HTTPException(400, str(e))
    return {**version_out(v, db), "restart": injector.RESTART_ENABLED}
