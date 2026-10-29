"""Work areas (1.0.80): Dev → Test → Live.

    Dev   one per builder: the live app copied, with THEIR Builder drafts laid in, on a port of its own and its
          own throwaway data (kept between starts, so test rows survive). Only they get a sign-in.
    Test  one, shared: a released version (tests passed) put there for everyone who tests to try before the
          owner promotes it. Every tester gets a sign-in of their own.
    Live  production. Promote takes a version that went through Test (or, on purpose, one that did not).

Who: a builder is an admin or anyone with "Propose code changes" (the Developer admin role); a tester is an
admin or anyone with "Run a sandbox". Promote stays the owner's. C-EXA never gets an area or a sign-in.

The areas are sandbox slots ("dev-<user id>", "test") on top of sandbox-a/-b, which stay C-EXA's builds and
uploaded versions. Their ports are kept in work_areas so they survive a restart; what runs is in memory.
"""
from __future__ import annotations

import os
import shutil
from typing import Optional

from sqlalchemy.orm import Session

from .. import VERSION
from ..audit import log
from ..config import settings
from ..db import utcnow
from ..models import AppVersion, User, WorkArea
from . import sandbox_runner as R

TEST = "test"


def port_base() -> int:
    try:
        return int(os.environ.get("CBX_AREA_PORT_BASE") or 8100)
    except ValueError:
        return 8100


def can_build(u: Optional[User]) -> bool:
    return bool(u is not None and not u.is_axl and (u.is_admin or u.can("code.propose")))


def can_test(u: Optional[User]) -> bool:
    return bool(u is not None and not u.is_axl and (u.is_admin or u.can("versions.sandbox") or u.can("code.propose")))


def dev_slot(u: User) -> str:
    return f"dev-{u.id}"


def may_enter(u: Optional[User], slot: str) -> bool:
    """The sandbox door: your own Dev area (the owner may look into any), Test if you test, A/B for admins."""
    if u is None or u.is_axl:
        return False
    if slot == TEST:
        return can_test(u)
    if slot.startswith("dev-"):
        return slot == dev_slot(u) or u.is_owner
    return u.is_admin


def load(db: Session) -> None:
    """Make every kept area known to the runner (at start, and before any area call)."""
    for a in db.query(WorkArea).all():
        R.register(a.slot, a.port)


def area(db: Session, kind: str, user: Optional[User] = None) -> WorkArea:
    """The Test area, or this person's Dev area — made (with the next free port) the first time."""
    slot = TEST if kind == "test" else dev_slot(user)
    a = db.query(WorkArea).filter(WorkArea.slot == slot).one_or_none()
    if a is None:
        if kind == "test":
            port = port_base()
        else:
            used = {x.port for x in db.query(WorkArea).all()} | set(R.DEFAULT_PORTS.values())
            port = port_base() + 1
            while port in used:
                port += 1
        a = WorkArea(slot=slot, kind="test" if kind == "test" else "dev", user_id=None if kind == "test" else user.id, port=port)
        db.add(a)
        db.flush()
    R.register(a.slot, a.port)
    return a


def testers(db: Session) -> list[tuple[str, str]]:
    from ..security.roles import attach

    out = []
    for u in db.query(User).filter(User.is_active.is_(True), User.is_axl.is_(False)).order_by(User.id).all():
        attach(db, u)
        if can_test(u) and u.email:
            out.append((u.email, u.name))
    return out


def _my_drafts(user: User) -> list:
    from ..api import builder_api as B

    root = B.drafts_root()
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and (d / "tool.json").exists():
            who = (B.editor_of(d) or {}).get("user_id")
            if who == user.id or (who is None and user.is_owner):  # unclaimed drafts (C-EXA's, or older) are the owner's
                out.append(d)
    return out


def start_dev(db: Session, me: User) -> dict:
    """The live app as it runs now, with my drafts laid into tools/, started on my port with my sign-in."""
    from ..api import builder_api as B
    from ..cexa import developer

    a = area(db, "dev", me)
    dest = developer.copy_live_tree(settings.path("sandboxes") / f"app-{a.slot}")
    drafts = _my_drafts(me)
    from ..engine import loader

    names = []
    for d in drafts:
        t = loader.read_folder(d)
        B.write_folder(dest / "tools" / t.slug, t.manifest, t.pages, t.data, t.languages, t.readme, t.flows)
        if (d / "assets").is_dir():
            shutil.copytree(d / "assets", dest / "tools" / t.slug / "assets", dirs_exist_ok=True)
        names.append(t.slug)
    title = f"{me.name.split(' ')[0]}'s dev area · v{VERSION}" + (f" + drafts: {', '.join(names)}" if names else "")
    out = R.start(a.slot, str(dest), owners=[(me.email, me.name)], kind="dev", title=title)
    a.started_at = utcnow()
    log(db, "work_area_started", actor=me, target=a.slot, detail=f"{title} → port {a.port}", outcome="ok" if out.get("ok") else "failed")
    return {**out, "drafts": names}


def start_test(db: Session, me: User, v: AppVersion) -> dict:
    """Put a released version in Test for everyone who tests. Only a version whose tests passed."""
    if v.status not in ("sandbox", "staged"):
        raise ValueError(f"Version {v.tag} is {v.status}: only a version waiting to go live can be tried in Test.")
    if v.status == "staged" or not (v.tests or {}).get("ok", v.status == "sandbox"):
        raise ValueError(f"Version {v.tag} has not passed its tests yet — Test is for versions that did.")
    a = area(db, "test")
    out = R.start(a.slot, v.package_path, owners=testers(db), kind="test", title=f"v{v.tag} in Test")
    a.version_id = v.id
    a.started_at = utcnow()
    v.tests = {**(v.tests or {}), "in_test": {"at": utcnow().isoformat(), "by": me.name}}
    log(db, "version_in_test", actor=me, target=v.tag, detail=f"Test area, port {a.port}; sign-ins for {len(out.get('login') or [])} tester(s)", outcome="ok" if out.get("ok") else "failed")
    return out


def stop(db: Session, me: User, slot: str) -> dict:
    out = R.stop(slot)
    log(db, "work_area_stopped", actor=me, target=slot)
    return out


def reset_data(db: Session, me: User) -> dict:
    """Empty my Dev area's data (it is kept between starts otherwise)."""
    a = area(db, "dev", me)
    R.stop(a.slot)
    d = settings.data_dir / "sandboxes" / f"{a.slot}-data"
    shutil.rmtree(d, ignore_errors=True)
    log(db, "work_area_reset", actor=me, target=a.slot)
    return {"ok": True}


def _state(slot: str, me: User) -> dict:
    st = next((r for r in R.status() if r["slot"] == slot), None) or {}
    mine = [{"email": x["email"], "password": x["password"]} for x in R.logins(slot) if x["email"].lower() == (me.email or "").lower()]
    return {"slot": slot, "running": bool(st.get("running")), "healthy": bool(st.get("healthy")), "title": st.get("title") or "", "port": st.get("port"),
            "door": f"/sandbox/{slot}/enter", "login": mine}


def overview(db: Session, me: User) -> dict:
    """What the Work areas page shows this person."""
    from ..api import builder_api as B
    from .injector import current_tag

    load(db)
    out: dict = {"live": {"version": current_tag()}, "can_build": can_build(me), "can_test": can_test(me), "can_promote": bool(me.is_owner)}
    if can_build(me):
        a = area(db, "dev", me)
        out["dev"] = {**_state(a.slot, me), "drafts": [d.name for d in _my_drafts(me)]}
        if me.is_owner:
            others = db.query(WorkArea).filter(WorkArea.kind == "dev", WorkArea.user_id != me.id).all()
            names = {u.id: u.name for u in db.query(User).filter(User.id.in_([x.user_id for x in others] or [0])).all()}
            out["others"] = [{**_state(x.slot, me), "who": names.get(x.user_id, "?")} for x in others]
    if can_test(me):
        a = area(db, "test")
        v = db.get(AppVersion, a.version_id) if a.version_id else None
        out["test"] = {**_state(a.slot, me), "version": v.tag if v else None}
        waiting = db.query(AppVersion).filter(AppVersion.status == "sandbox").order_by(AppVersion.created_at.desc()).limit(10).all()
        out["ready"] = [{"id": x.id, "tag": x.tag, "notes": (x.notes or "")[:200], "in_test": bool((x.tests or {}).get("in_test"))} for x in waiting]
    out["drafts_taken"] = [{"slug": d.name, "who": (B.editor_of(d) or {}).get("name", "")} for d in (sorted(B.drafts_root().iterdir()) if B.drafts_root().is_dir() else [])
                           if d.is_dir() and (B.editor_of(d) or {}).get("user_id") not in (None, me.id)]
    return out
