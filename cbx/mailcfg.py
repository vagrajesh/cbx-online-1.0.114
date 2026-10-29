"""Mail settings kept in CBX (1.0.70), not only in .env — so they can be changed on the Mailer page without
opening the server and restarting.

    enabled        CBX may send mail at all
    transport      "outlook" (through Outlook on this server) or "smtp"
    send_store     the mailbox in Outlook mail leaves from (its name as Outlook shows it)
    send_address   that mailbox's address (Outlook sends "as" it)
    drafts         which automatic mail waits as a draft for a person (1.0.71): "support" (every ticket
                   mail) or "support:<template>", "reminder", "flow"
    boxes          the support mailboxes — every new mail in each becomes a ticket:
                   [{store, address, folder, on, since}]   since: read mail received from then on (ISO, UTC)

Anything not saved here falls back to .env (CBX_MAIL_ENABLED, CBX_MAIL_TRANSPORT, CBX_OUTLOOK_MAILBOX,
CBX_OUTLOOK_FOLDER, CBX_SUPPORT_MAIL), so a server set up through .env keeps working unchanged.
"""
from __future__ import annotations

import json
import time
from typing import Any

from .config import settings

KEY = "mail.settings"
_saved: dict[str, Any] = {"v": None, "at": 0.0}


def defaults() -> dict:
    mb = (settings.outlook_mailbox or "").strip()
    boxes = [{"store": mb, "address": mb if "@" in mb else "", "folder": settings.outlook_folder or "Inbox", "on": bool(settings.support_mail), "since": None}] if mb else []
    return {"enabled": bool(settings.mail_enabled), "transport": (settings.mail_transport or "smtp").lower(), "send_store": mb,
            "send_address": mb if "@" in mb else (settings.mail_from or ""), "boxes": boxes,
            "drafts": [x.strip() for x in (settings.mail_drafts or "").split(",") if x.strip()]}


def saved() -> dict:
    if _saved["v"] is not None and time.time() - _saved["at"] < 30:
        return _saved["v"]
    v: dict = {}
    try:
        from . import state
        from .db import SessionLocal

        db = SessionLocal()
        try:
            v = json.loads(state.get(db, KEY) or "{}")
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — before the tables exist; .env alone then
        v = {}
    _saved.update(v=v if isinstance(v, dict) else {}, at=time.time())
    return _saved["v"]


def get() -> dict:
    return {**defaults(), **saved()}


def save(db, changes: dict) -> dict:
    from . import state

    cur = dict(saved())
    cur.update(changes)
    state.set(db, KEY, json.dumps(cur))
    _saved.update(v=cur, at=time.time())
    return get()


def forget() -> None:
    """Drop the cached copy (tests, and after a restore)."""
    _saved.update(v=None, at=0.0)


def support_stores() -> set[str]:
    return {(b.get("store") or "").strip().lower() for b in get()["boxes"] if b.get("store")} | {(b.get("address") or "").strip().lower() for b in get()["boxes"] if b.get("address")}


def desk_address() -> str:
    """1.0.109: the support desk's own address (the first support mailbox that is read, else the address CBX sends from) — what a
    requester is told to write to for a new ticket."""
    c = get()
    for b in c["boxes"]:
        if b.get("on"):
            for a in (b.get("address"), b.get("store")):
                if a and "@" in a:
                    return a.strip()
    return (c.get("send_address") or "").strip()


def held(key: str) -> bool:
    """Does mail of this kind wait as a draft? ("support:asked" is held by "support" or "support:asked".)"""
    if not key:
        return False
    d = set(get().get("drafts") or [])
    return key in d or key.split(":", 1)[0] in d
