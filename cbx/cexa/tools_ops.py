"""C-EXA runs the app (1.0.81): people and files, as plain tools over the app's own API.

Every tool here is a call of the API as the person she works for (tools.api), so the same rules hold as for a
person clicking — and cbx/cexa/policy.py decides what she does alone and what waits for the owner's one tap:
a plain user, details, a password reset, a folder, moving or renaming a file she does; admin or owner rights,
access to a tool, disabling someone, the Bin wait for the owner. Passwords never reach the model.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Fix, Folder, Tool, User
from .tools import api, tool


def _who(db: Session, ref: str) -> Optional[User]:
    ref = (ref or "").strip()
    if not ref:
        return None
    q = db.query(User).filter(User.is_axl.is_(False))
    if ref.isdigit():
        return q.filter(User.id == int(ref)).first()
    if "@" in ref:
        return q.filter(User.email.ilike(ref)).first()
    hits = [u for u in q.all() if ref.lower() in (u.name or "").lower()]
    return hits[0] if len(hits) == 1 else None


def _many(db: Session, ref: str) -> str:
    hits = [u for u in db.query(User).filter(User.is_axl.is_(False)).all() if (ref or "").lower() in (u.name or "").lower()]
    if len(hits) > 1:
        return "More than one person matches: " + ", ".join(f"{u.name} <{u.email}>" for u in hits[:8]) + ". Say which (by e-mail)."
    return f"I can't find '{ref}'. Use list_people."


# ---- people -------------------------------------------------------------------------------------------------------

@tool("list_people", "read", "Everyone on CBX (or those matching a word): name, e-mail, role, team, active or not, locked, last seen, tools.", {"search": "optional: part of a name, e-mail, team or role"})
def list_people(db: Session, me: User, fix: Optional[Fix], search: str = "", **_) -> str:
    if not me.is_admin and not me.is_manager:
        return "People are for admins and managers."
    from ..models import ToolAccess

    w = (search or "").lower().strip()
    names = {t.id: t.slug for t in db.query(Tool).all()}
    held: dict = {}
    for a in db.query(ToolAccess).all():
        held.setdefault(a.user_id, []).append(names.get(a.tool_id, "?"))
    out = []
    people = db.query(User).filter(User.is_axl.is_(False)).order_by(User.name).all()
    if me.is_team_limited and not me.is_admin:
        # R8: asking C-EXA is not a way round "their own team only" — the same people Users & roles shows them
        from ..api.users import _visible_users

        people = sorted((u for u in _visible_users(db, me) if not u.is_axl), key=lambda u: u.name)
    for u in people:
        tools = ", ".join(sorted(held.get(u.id, [])))
        line = f"#{u.id} {u.name} <{u.email}> · {u.role} · {u.team.name if u.team else 'no team'} · {'active' if u.is_active else 'disabled'}{' · locked' if u.locked else ''}{' · must change password' if u.must_change_password else ''}"
        if tools:
            line += f" · tools: {tools}"
        if not w or w in line.lower():
            out.append(line)
    return "\n".join(out[:80]) or "Nobody matches."


@tool("add_person", "change", "Add someone to CBX. A plain user I add myself; an admin, an owner or tools on day one wait for the owner's tap. Their temporary password goes to you in Messages, never to me.", {"email": "their work e-mail", "name": "full name", "role": "user (default) | manager | admin | owner", "team": "team name (made if new)", "title": "optional job title", "ends": "optional YYYY-MM-DD for outside people"})
def add_person(db: Session, me: User, fix: Optional[Fix], email: str = "", name: str = "", role: str = "user", team: str = "", title: str = "", ends: str = "", **_) -> str:
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", (email or "").strip()):
        return "Give their work e-mail."
    body = {"email": email.strip(), "name": (name or email.split("@")[0]).strip(), "role": (role or "user").strip().lower(), "title": title or "", "team_name": team or None}
    if ends:
        body["expires_at"] = ends
    return api(db, me, "POST", "/api/users", body, why=f"Add {body['name']} as {body['role']}")


@tool("change_person", "change", "Change someone: name, title, team, role, unlock, end date, or disable / enable. Details and unlocking I do; a role above user and disabling wait for the owner's tap.", {"who": "name, e-mail or #id", "name": "new name", "title": "new title", "team": "team name", "role": "new role", "unlock": "yes to unlock", "active": "no to disable, yes to enable", "ends": "YYYY-MM-DD, or none"})
def change_person(db: Session, me: User, fix: Optional[Fix], who: str = "", name: str = "", title: str = "", team: str = "", role: str = "", unlock: str = "", active: str = "", ends: str = "", **_) -> str:
    u = _who(db, who.lstrip("#"))
    if u is None:
        return _many(db, who)
    body: dict = {}
    if name:
        body["name"] = name
    if title:
        body["title"] = title
    if role:
        body["role"] = role.strip().lower()
    if unlock.strip().lower().startswith("y"):
        body["unlock"] = True
    if active:
        body["is_active"] = not active.strip().lower().startswith(("n", "f", "0", "dis"))
    if ends:
        body["expires_at"] = "" if ends.strip().lower() in ("none", "never", "no") else ends.strip()
    if team:
        from ..models import Team

        t = db.query(Team).filter(Team.name.ilike(team.strip())).first()
        if t is None:
            made = api(db, me, "POST", "/api/users/teams", {"name": team.strip()}, why=f"Make the team {team}")
            t = db.query(Team).filter(Team.name.ilike(team.strip())).first()
            if t is None:
                return made
        body["team_id"] = t.id
    if not body:
        return "What should change?"
    what = ", ".join(f"{k} → {v}" for k, v in body.items())
    return api(db, me, "PATCH", f"/api/users/{u.id}", body, why=f"{u.name}: {what}")


@tool("reset_password", "change", "Give someone a new temporary password (they choose their own at the next sign-in; their other sessions end). The password goes to you in Messages, never to me.", {"who": "name, e-mail or #id"})
def reset_password(db: Session, me: User, fix: Optional[Fix], who: str = "", **_) -> str:
    u = _who(db, who.lstrip("#"))
    if u is None:
        return _many(db, who)
    return api(db, me, "POST", f"/api/users/{u.id}/temp-password", {}, why=f"Reset {u.name}'s password")


@tool("give_access", "change", "Give or take someone's access to a tool (PDF Studio, IFRS 16, …). Always waits for the owner's tap.", {"who": "name, e-mail or #id", "tool": "tool name or slug", "on": "yes to give, no to take away"})
def give_access(db: Session, me: User, fix: Optional[Fix], who: str = "", tool: str = "", on: str = "yes", **_) -> str:
    u = _who(db, who.lstrip("#"))
    if u is None:
        return _many(db, who)
    w = (tool or "").strip().lower()
    t = next((x for x in db.query(Tool).filter(Tool.is_active.is_(True)).all() if w in (x.slug.lower(), (x.name or "").lower())), None)
    if t is None:
        return "Which tool? " + ", ".join(x.name for x in db.query(Tool).filter(Tool.is_active.is_(True)).order_by(Tool.sort))
    give = not str(on).strip().lower().startswith(("n", "f", "0", "take", "remove"))
    return api(db, me, "POST", "/api/users/access", {"user_id": u.id, "tool_id": t.id, "granted": give}, why=f"{'Give' if give else 'Take away'} {u.name} {'access to' if give else 'from'} {t.name}")


# ---- files --------------------------------------------------------------------------------------------------------

@tool("list_files", "read", "Find files in the data warehouse by words in their name or folder (newest first): id, name, folder path, kind, who added it.", {"search": "words, e.g. 'telkom march' or 'Inbox Thandi'"})
def list_files(db: Session, me: User, fix: Optional[Fix], search: str = "", **_) -> str:
    from urllib.parse import quote

    res = api(db, me, "GET", f"/api/warehouse/find?q={quote(search or '')}&limit=60")
    m = re.search(r"→ 200\. (.*)$", res, re.S)
    if not m:
        return res
    try:
        rows = json.loads(m.group(1)).get("files", [])
    except ValueError:
        return res
    return "\n".join(f"#{f['id']} {f['path']} · {f['kind']} · {f.get('uploaded_by') or '—'}" for f in rows) or "No file matches."


def _folder(db: Session, ref: str) -> Optional[Folder]:
    ref = (ref or "").strip().strip("/")
    if ref.isdigit():
        return db.get(Folder, int(ref))
    return db.query(Folder).filter(Folder.path.ilike(ref)).first() or next(iter(db.query(Folder).filter(Folder.path.ilike(f"%{ref}")).limit(1).all()), None)


@tool("move_file", "change", "Move a file to another folder of the warehouse (not into or out of a tool's own folders).", {"file": "file #id", "folder": "folder path (e.g. 'Shared library/PDFs') or #id"})
def move_file(db: Session, me: User, fix: Optional[Fix], file: str = "", folder: str = "", **_) -> str:
    d = _folder(db, folder.lstrip("#"))
    if d is None:
        return f"No folder '{folder}'."
    fid = re.sub(r"\D", "", file or "")
    if not fid:
        return "Which file (#id from list_files)?"
    return api(db, me, "PATCH", f"/api/warehouse/files/{fid}", {"folder_id": d.id}, why=f"Move file #{fid} to {d.path}")


@tool("rename_file", "change", "Rename a file in the warehouse (the extension is kept).", {"file": "file #id", "name": "the new name"})
def rename_file(db: Session, me: User, fix: Optional[Fix], file: str = "", name: str = "", **_) -> str:
    fid = re.sub(r"\D", "", file or "")
    if not fid or not (name or "").strip():
        return "Which file (#id) and what name?"
    return api(db, me, "PATCH", f"/api/warehouse/files/{fid}", {"name": name.strip()}, why=f"Rename file #{fid} to {name.strip()}")


@tool("make_folder", "change", "Make a folder in the warehouse, inside an existing one (not under Tools/).", {"inside": "the parent folder's path or #id", "name": "the new folder's name"})
def make_folder(db: Session, me: User, fix: Optional[Fix], inside: str = "", name: str = "", **_) -> str:
    p = _folder(db, inside.lstrip("#"))
    if p is None:
        return f"No folder '{inside}'."
    return api(db, me, "POST", "/api/warehouse/folders", {"parent_id": p.id, "name": name}, why=f"Make the folder {p.path}/{name}")


@tool("delete_file", "change", "Put a file in the Bin (it keeps its links and can be moved back). Always waits for the owner's tap.", {"file": "file #id", "why": "why it should go"})
def delete_file(db: Session, me: User, fix: Optional[Fix], file: str = "", why: str = "", **_) -> str:
    fid = re.sub(r"\D", "", file or "")
    if not fid:
        return "Which file (#id from list_files)?"
    return api(db, me, "DELETE", f"/api/warehouse/files/{fid}", None, why=f"Put file #{fid} in the Bin" + (f" — {why}" if why else ""))


# ---- teams of agents (1.0.82) -------------------------------------------------------------------------------------

@tool("make_team", "change", "Make a new team of agents for a standing job: a working team (work) does it with its hands in the app — people, files, rows, tools; or one that builds, changes or advises. It gets a lead, an operator and a helper unless members are given. Optional schedule.", {"name": "the team's name, e.g. Telkom billing", "purpose": "one line: what the team is for (its standing order)", "kind": "work (default) | build | change | advice", "every": "optional: day | weekday | week | month", "at": "optional HH:MM for the schedule"})
def make_team(db: Session, me: User, fix: Optional[Fix], name: str = "", purpose: str = "", kind: str = "work", every: str = "", at: str = "", **_) -> str:
    body: dict = {"name": name, "purpose": purpose, "kind": (kind or "work").strip().lower()}
    if every:
        body["schedule"] = {"every": every.strip().lower(), "at": (at or "07:00").strip()}
    return api(db, me, "POST", "/api/cexa/teams", body, why=f"Make the team {name}")[:1500]


@tool("give_team_work", "change", "Give a team of agents a piece of work: its lead takes it, plans it and hands parts to the others.", {"team": "the team's name or key", "title": "the work in one line", "detail": "what done looks like, and anything it needs"})
def give_team_work(db: Session, me: User, fix: Optional[Fix], team: str = "", title: str = "", detail: str = "", **_) -> str:
    from ..models import AgentTeam
    from .teams import lead_of

    w = (team or "").strip().lower()
    t = next((x for x in db.query(AgentTeam).all() if w in (x.key.lower(), (x.name or "").lower())), None)
    if t is None:
        return "Which team? " + ", ".join(x.name for x in db.query(AgentTeam).order_by(AgentTeam.sort).all())
    lead = lead_of(db, t)
    if lead is None or not t.enabled:
        return f"{t.name} is switched off or has no lead."
    return api(db, me, "POST", "/api/cexa/work", {"to": lead.name, "title": title, "detail": detail}, why=f"Give {t.name} work: {title}")[:1200]
