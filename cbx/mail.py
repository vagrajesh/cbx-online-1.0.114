"""Outbound email, from the app's own mailbox.

Until now CBX could not send mail at all: the compliance reminders were counted in
`watched_docs.reminders_sent` and shown in the app, which only reaches someone who happens to open
it. A certificate expiring in ten days needs to reach the person who renews it, wherever they are.

Nothing here fails loudly. If mail is not configured, `send()` returns a `Result` saying so and the
caller carries on — a batch, a reminder sweep or a sign-in must never break because SMTP is down.
Every attempt, sent or not, is returned to the caller so it can be written to the audit trail.

Configure in `.env` on the server:

    CBX_MAIL_FROM=cbx-connect@cbreexcellerate.com
    CBX_SMTP_HOST=smtp.office365.com
    CBX_SMTP_PORT=587
    CBX_SMTP_USER=cbx-connect@cbreexcellerate.com
    CBX_SMTP_PASSWORD=...        # never in the repo, never in a version package
    CBX_SMTP_STARTTLS=true

`CBX_MAIL_ENABLED` defaults to false: the switch is deliberate, so a test server restored from a
backup cannot start mailing real people about real expiry dates.
"""
from __future__ import annotations

import logging
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Iterable, Optional

from .config import settings

log_ = logging.getLogger("cbx.mail")
ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class Result:
    ok: bool
    detail: str  # a sentence for the audit trail, or for a person to act on
    to: tuple[str, ...] = ()
    # 1.0.109: what the sent mail is known by — its Message-ID (SMTP sets it; through Outlook it is read back later from Sent
    # Items) and Outlook's conversation — so a reply without the ticket number still finds its ticket (support/inbox.find_ticket)
    message_id: str = ""
    conversation: str = ""

    def __bool__(self) -> bool:
        return self.ok


def only_to() -> list[str]:
    """Addresses every message is redirected to while CBX_MAIL_ONLY_TO is set."""
    return [a.strip() for a in (settings.mail_only_to or "").split(",") if a.strip()]


def status() -> dict:
    """What the Email settings panel shows: is it on, what is missing, and is it redirected.

    The redirect is reported loudly on purpose. Mail that silently goes somewhere other than where
    the app says it went is worse than no mail at all."""
    ready, why = configured()
    return {
        "enabled": bool(_cfg()["enabled"]),
        "ready": ready,
        "detail": why,
        "mail_from": settings.mail_from,
        "smtp_host": settings.smtp_host,
        "transport": _cfg()["transport"],
        "outlook_mailbox": _cfg()["send_store"],
        "smtp_port": settings.smtp_port,
        "authenticated": bool(settings.smtp_user),
        "starttls": bool(settings.smtp_starttls),
        "only_to": only_to(),
        "daily_cap": settings.mail_daily_cap,
        "sent_today": _sent_today()[1],
    }


_sent: dict = {"day": None, "n": 0}


def _sent_today() -> tuple[object, int]:
    from datetime import date

    today = date.today()
    if _sent["day"] != today:
        _sent["day"], _sent["n"] = today, 0
    return today, _sent["n"]


def configured() -> tuple[bool, str]:
    """Can this server send mail, and if not, what is missing — named, so it can be fixed."""
    if not _cfg()["enabled"]:
        return False, "Email is switched off on this server (Mailer → Mail set-up)."
    if _cfg()["transport"] == "outlook":  # 1.0.66: through Outlook on this server
        from .support import outlook

        return outlook.available()
    missing = [n for n, v in (("CBX_MAIL_FROM", settings.mail_from), ("CBX_SMTP_HOST", settings.smtp_host)) if not v]
    if missing:
        return False, f"Email is not set up yet — {' and '.join(missing)} still needs a value in .env."
    return True, f"Sending as {settings.mail_from} through {settings.smtp_host}."


def _cfg() -> dict:
    from . import mailcfg

    return mailcfg.get()


def valid(address: str) -> bool:
    return bool(ADDRESS.match((address or "").strip()))


def prepare(people: list[str], subject: str, body: str, html: Optional[str]) -> tuple[list[str], str, str, Optional[str]]:
    """What actually leaves: while CBX_MAIL_ONLY_TO is set, it goes there instead — and says who it was for."""
    redirect = only_to()
    if redirect:
        body = f"[Redirected. This would have gone to: {', '.join(people)}]\n\n" + body
        if html:
            html = f"<p style=\"background:#fff4d6;padding:8px;border-radius:6px\"><b>Redirected.</b> This would have gone to: {', '.join(people)}</p>" + html
        subject = f"[test] {subject}"
        people = redirect
    return people, subject, body, html


def _capped(people: list[str]) -> Optional[Result]:
    _, n = _sent_today()
    if settings.mail_daily_cap and n >= settings.mail_daily_cap:
        return Result(False, f"Daily send limit reached ({settings.mail_daily_cap}). Nothing more will go out today — raise CBX_MAIL_DAILY_CAP if this is expected.", tuple(people))
    return None


def send(to: Iterable[str], subject: str, body: str, *, html: Optional[str] = None, reply_to: str = "", thread: str = "", mark: str = "",
         attachments: Optional[list] = None, inline: Optional[list] = None) -> Result:
    """Send one message. Never raises — the caller's work matters more than the mail.
    `thread` (1.0.71): the Outlook mail this answers — through Outlook it goes as a reply in that thread.
    `attachments` (1.0.109): [(name, bytes)] — the requester's pictures on the lead's ticket mail."""
    people = [a.strip() for a in to if valid(a)]
    if not people:
        return Result(False, "Nobody to send to — none of those addresses look like an email address.")
    ready, why = configured()
    if not ready:
        return Result(False, why, tuple(people))

    # A runaway loop should stop here, not in someone's inbox.
    cap = _capped(people)
    if cap is not None:
        return cap

    # Redirected: the message still says who it was really for, so a test run is readable.
    people, subject, body, html = prepare(people, subject, body, html)

    if _cfg()["transport"] == "outlook":
        from .support import outlook

        info: dict = {}
        ok, detail = outlook.send(people, subject, body, html=html, reply_to=reply_to, thread=thread, mark=mark, **({"attachments": attachments} if attachments else {}),
                                  **({"inline": inline} if inline else {}), info=info)
        if ok:
            _sent["n"] += 1
        return Result(ok, detail, tuple(people), message_id=str(info.get("message_id") or "")[:400], conversation=str(info.get("conversation") or "")[:200])

    msg = EmailMessage()
    from email.utils import make_msgid

    mid = make_msgid(domain=(settings.mail_from.rsplit("@", 1)[-1] if "@" in (settings.mail_from or "") else "cbx.local"))  # 1.0.109
    msg["Message-ID"] = mid
    msg["From"] = settings.mail_from
    msg["To"] = ", ".join(people)
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
        if inline:  # 1.0.109 (round 2): pictures inside the message (cid:), a multipart/related HTML part
            import mimetypes

            part = msg.get_payload()[-1]
            for name, data, cid in inline:
                typ = (mimetypes.guess_type(name or "")[0] or "image/png").split("/", 1)
                part.add_related(data, maintype=typ[0], subtype=typ[1], cid=f"<{cid}>", filename=name, disposition="inline")
    for name, data in attachments or []:  # 1.0.109
        import mimetypes

        typ = (mimetypes.guess_type(name or "")[0] or "application/octet-stream").split("/", 1)
        msg.add_attachment(data, maintype=typ[0], subtype=typ[1], filename=name)

    try:
        if settings.smtp_port == 465:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30, context=ssl.create_default_context()) as s:
                _login(s)
                s.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as s:
                if settings.smtp_starttls:
                    s.starttls(context=ssl.create_default_context())
                _login(s)
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return Result(False, "The mailbox refused the username and password. Check CBX_SMTP_USER and CBX_SMTP_PASSWORD.", tuple(people))
    except (smtplib.SMTPException, OSError, ssl.SSLError) as e:  # noqa: BLE001 — every failure is reported, never raised
        log_.warning("mail to %s failed: %s", people, e)
        return Result(False, f"Could not reach the mail server: {e}", tuple(people))
    _sent["n"] += 1
    return Result(True, f"Sent to {', '.join(people)}.", tuple(people), message_id=mid)


def save_draft(to: Iterable[str], subject: str, body: str, *, html: Optional[str] = None, reply_to: str = "", thread: str = "", mark: str = "") -> tuple[str, str]:
    """1.0.71: through Outlook, also keep the held mail as an Outlook draft (redirect applied, so sending it
    from Outlook still obeys a test run). Returns (its id or "", a sentence)."""
    if _cfg()["transport"] != "outlook" or not configured()[0]:
        return "", ""
    people = [a.strip() for a in to if valid(a)]
    if not people:
        return "", ""
    from .support import outlook

    people, subject, body, html = prepare(people, subject, body, html)
    try:
        return outlook.save_draft(people, subject, body, html=html, reply_to=reply_to, thread=thread, mark=mark), "Also in Outlook → Drafts."
    except Exception as e:  # noqa: BLE001 — it still waits in CBX
        log_.warning("outlook draft failed: %s", e)
        return "", f"Not saved in Outlook ({str(e)[:120]}) — it waits in CBX only."


def send_saved_draft(entry_id: str, people: list[str]) -> Result:
    """Send the Outlook draft as it is (a person checked it)."""
    ready, why = configured()
    if not ready:
        return Result(False, why, tuple(people))
    cap = _capped(people)
    if cap is not None:
        return cap
    from .support import outlook

    try:
        outlook.send_draft(entry_id)
    except Exception as e:  # noqa: BLE001
        return Result(False, str(e)[:300], tuple(people))
    _sent["n"] += 1
    return Result(True, f"Sent through Outlook to {', '.join(only_to() or people)}.", tuple(only_to() or people))


def _login(s: smtplib.SMTP) -> None:
    if settings.smtp_user:
        s.login(settings.smtp_user, settings.smtp_password)
