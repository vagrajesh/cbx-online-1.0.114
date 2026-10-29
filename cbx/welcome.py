"""1.0.93: the welcome mail a new person gets when their account is made — Edwin's own words (12 Oct): where to go,
the email and temporary password to sign in with, that they choose their own password at once. Someone given
Entity Compliance also reads what it is and which entities they are the responsible person for.

Sent through the Mailer like every other automatic mail (kind "welcome"; the Mailer's Settings can hold it as a
draft to check first). Never raises: the account is made either way, and the person who made it is told whether
the mail went.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from .models import Tool, ToolAccess, User

EC_TOOL = "doc-watch"  # Entity Compliance's grant slug (the name people see is Entity Compliance)
EC_FOLDER = "entity-compliance"


def _first(name: str) -> str:
    return (name or "").strip().split(" ")[0] or "there"


def _and(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def entities_for(db: Session, u: User) -> list[str]:
    """The entities this person is the responsible person for (by email or account), or linked to — from the Entity
    Compliance tool's companies, else the older entities table."""
    names: list[str] = []
    email = (u.email or "").strip().lower()
    try:
        from .engine import loader
        from .engine.store import Store

        t = loader.registry().tools.get(EC_FOLDER)
        st = Store(EC_FOLDER)
        if t is not None and "companies" in t.data and st.exists("companies"):
            cols = t.data["companies"].get("columns") or []
            linked: set[int] = set()
            if "company_people" in t.data and st.exists("company_people"):
                for r in st.rows("company_people", t.data["company_people"].get("columns") or [], limit=5000)["rows"]:
                    if str(r.get("person") or "") == str(u.id) and str(r.get("company") or "").isdigit():
                        linked.add(int(r["company"]))
            for r in st.rows("companies", cols, limit=5000)["rows"]:
                if r.get("archived") in (True, 1, "1", "true", "Yes"):
                    continue
                mine = str(r.get("responsible_email") or "").strip().lower() == email or str(r.get("responsible_account") or "") == str(u.id) or int(r["id"]) in linked
                if mine and r.get("name"):
                    names.append(str(r["name"]).strip())
    except Exception:  # noqa: BLE001 — the mail still goes without the list
        names = []
    if not names:
        try:
            from .models.docs import Entity

            q = db.query(Entity).filter(Entity.is_demo.is_(False))
            names = [e.name for e in q.all() if (e.responsible_email or "").strip().lower() == email or e.responsible_user_id == u.id]
        except Exception:  # noqa: BLE001
            names = []
    return sorted(dict.fromkeys(names), key=str.lower)


def tools_of(db: Session, u: User) -> list[str]:
    return [t.slug for t in db.query(Tool).join(ToolAccess, ToolAccess.tool_id == Tool.id).filter(ToolAccess.user_id == u.id).all()]


def compose(db: Session, u: User, temp_password: str, by: Optional[User]) -> tuple[str, str]:
    """(subject, body) in Edwin's words."""
    from . import state
    from .branding import product_name

    url = state.public_url(db).rstrip("/") + "/"
    product = product_name()
    slugs = tools_of(db, u)
    ec = EC_TOOL in slugs or u.role in ("owner", "admin")
    ents = entities_for(db, u) if ec else []
    lines = [f"Hi {_first(u.name)}", ""]
    if ec:
        lines.append("You now have access to our new Entity Compliance system — it tracks every legal entity's compliance documents "
                     "(bank letters, tax clearance, COIDA, etc.) in one place, with automatic reminders before anything expires.")
        if ents:
            lines += ["", f"Your entities include {_and(ents)}, and you're shown as the responsible person for {'them' if len(ents) > 1 else 'it'}."]
    else:
        names = [t.name for t in db.query(Tool).filter(Tool.slug.in_(slugs or ["-"])).order_by(Tool.sort).all()]
        lines.append(f"You now have access to {product}" + (f" — {_and(names)}." if names else "."))
    lines += ["", "To get in:", "", f"1. Go to: {url}"]
    n = 2
    if "ngrok" in url:
        lines.append(f"{n}. (First visit only: you may see an ngrok warning page — click \"Visit Site\" to continue.)")
        n += 1
    lines += [f"{n}. Sign in with:", f"   Email: {u.email}", f"   Password: {temp_password}"]
    lines.append(f"{n + 1}. You'll be asked to set your own password immediately — pick something you'll remember (12+ characters).")
    lines.append("")
    if ec:
        lines.append("Please have a look when you get a chance — documents for your entities will be collected there going forward, "
                     "and where something is not applicable you can mark it as such.")
    else:
        lines.append("Please have a look when you get a chance.")
    lines += ["", "Thanks"] + ([_first(by.name)] if by and by.name else [])
    subject = "Your access to Entity Compliance" if ec else f"Your access to {product}"
    return subject, "\n".join(lines)


def send(db: Session, u: User, temp_password: str, by: Optional[User]) -> dict:
    """Send it (or hold it as a draft when the Mailer says so). {ok, detail} for the person who made the account."""
    from . import mail, mailbox, mailcfg

    try:
        ready, why = mail.configured()
        if not ready and not mailcfg.held("welcome"):  # mail is off: say so, and leave no failed mail behind
            return {"ok": False, "held": False, "detail": why.rstrip(".") + "."}
        subject, body = compose(db, u, temp_password, by)
        row = mailbox.send(db, to=[u.email], subject=subject, body=body, kind="welcome", ref=f"user:{u.id}", by=by, hold="welcome",
                           reply_to=(by.email if by and by.email else ""))
        if temp_password and temp_password in (row.preview or ""):
            row.preview = row.preview.replace(temp_password, "(temporary password)")  # never readable in the Sent list
        held = (row.detail or "").startswith("draft:")
        return {"ok": bool(row.ok) or held, "held": held, "detail": row.detail or ""}
    except Exception as e:  # noqa: BLE001 — the account is made either way
        return {"ok": False, "held": False, "detail": f"The welcome mail could not be made: {str(e)[:200]}"}
