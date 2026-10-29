"""Ticket mails (1.0.71): what CBX writes to a requester or a lead, and how it looks.

Each mail is a template the owner can change on Mailer → Mail set-up → Ticket mails (kept in
app_state "support.mail.templates"; only the changed ones are stored, Reset brings the wording back).
The ticket number always goes in front of the subject — replies are matched on it.

    {first_name} {requester_name} {ref} {title} {system} {questions} {how_to_reply} {lead_first}
    {staff_first} {staff_name} {message} {what_was_done} {summary} {requester} {assigned_by} {where} {outcome} {reason_line}
    {missing} {days} {owner_first} {owner_name} {lead}  (1.0.109 round 2: the follow-up and the escalation)

`build()` dresses a message for sending: a plain-text version and a simple HTML one (a heading line, the
message, a button to the ticket, a reply hint and — when the mail does not go into the requester's own
thread — the earlier messages on the ticket underneath, so the chain is there either way).
"""
from __future__ import annotations

import html as H
import json
import re
from typing import Optional

KEY = "support.mail.templates"

# key: (who gets it, label, subject, body)
DEFAULTS: dict[str, tuple[str, str, str, str]] = {
    "asked": ("requester", "C-EXA asks for more information", "We need a little more information",
              "Hi {first_name},\n\nThank you — your ticket number is {ref}.\n\nTo sort this out, the support team needs a little more:\n{questions}\n\n{how_to_reply}"),
    "ready": ("requester", "The ticket has everything", "Your ticket is with the support team",
              "Hi {first_name},\n\nThank you — your ticket number is {ref}. It has everything the support team needs. Someone will pick it up and keep you posted."),
    "assigned": ("requester", "A lead picked it up", "Someone is working on your ticket",
                 "Hi {first_name},\n\nYour ticket {ref} is with {lead_first} from the support team, who will be in touch."),
    # 1.0.98: the lead is invisible — no name, no address (My settings)
    "assigned_hidden": ("requester", "A lead picked it up (the lead is invisible)", "Someone is working on your ticket",
                        "Hi {first_name},\n\nYour ticket {ref} has been assigned to the responsible person, who will be in touch through this desk.\n\n{how_to_reply}"),
    "lead_asks": ("requester", "The support person needs more", "{lead_first} needs a little more",
                  "Hi {first_name},\n\n{lead_first} from the support team, who is working on your ticket {ref}, needs a little more:\n\n{message}\n\n{how_to_reply}"),
    "lead_asks_hidden": ("requester", "The support person needs more (the lead is invisible)", "The support team needs a little more",
                         "Hi {first_name},\n\nFrom the support desk, on your ticket {ref} — the responsible person needs a little more:\n\n{message}\n\n{how_to_reply}"),
    "message": ("requester", "A message from the team", "Message from {staff_first}",
                "Hi {first_name},\n\n{message}\n\nKind regards\n{staff_name}\nCBX-Connect Support"),
    "done": ("requester", "The ticket is done", "Your ticket is done",
             "Hi {first_name},\n\nYour ticket {ref} is done.\n\nWhat was done: {what_was_done}\n\nIf it isn't sorted, just reply to this e-mail and it opens again."),
    # 1.0.103: the owner or an admin closed it as not relevant and ticked "Tell the requester" ({reason_line}: the reason, if given)
    "not_relevant": ("requester", "Closed as not relevant", "We closed your request",
                     "Hi {first_name},\n\nWe closed your request {ref} because it isn't something the support desk handles.{reason_line}"),
    # 1.0.109: the owner or an admin removed the ticket and chose "Let the requester know" ({message}: what they wrote, from
    # "Hi <name>, we've closed T-0012 without action: <reason>.")
    "removed": ("requester", "Removed — the requester is told", "We closed your request", "{message}"),
    # 1.0.109: the requester sent a new problem as a reply to an old ticket — asked to send it as a new mail ({support_address}: the desk's)
    "new_problem": ("requester", "A new problem sent in an old ticket's thread", "Please send this as a new e-mail",
                    "Hi {first_name}, this looks like a new problem, not part of {ref} ({title}). Please send it as a new e-mail to {support_address} "
                    "(not a reply to an old ticket), so it gets its own ticket number."),
    "lead_assigned": ("lead", "To the lead: assigned to you", "Assigned to you: {title}",
                      "{assigned_by} assigned you a support ticket. Press Accept when you take it on — the time it takes counts from then, and it goes on your To-do.\n\n{ref} — {title}\nWhere the problem is: {where}\nWhat must be done: {outcome}\nSystem: {system}\nFrom: {requester}\n\nSummary:\n{summary}\n\nNeed more from them? Reply to this e-mail — C-EXA asks them and sends you the answer. When it's done, press Mark as done."),
    # 1.0.106: the person took a free ticket themselves — it is accepted at once, so the mail has no Accept
    "lead_taken": ("lead", "To the person who took a free ticket", "Assigned to you: {title}",
                   "You took this support ticket. It is accepted and on your To-do — the time it takes counts from now.\n\n{ref} — {title}\nWhere the problem is: {where}\nWhat must be done: {outcome}\nSystem: {system}\nFrom: {requester}\n\nSummary:\n{summary}\n\nNeed more from them? Reply to this e-mail — C-EXA asks them and sends you the answer. When it's done, press Mark as done. Can't do it after all? Give it back on the ticket in CBX."),
    "lead_replied": ("lead", "To the lead: the requester replied", "{requester_name} replied",
                     "{title}\n\nThey wrote:\n\n{message}"),  # 1.0.109 (round 2): the message its own paragraph (its pictures in place)
    # 1.0.109 (round 2, cbx/support/followup.py): no reply from the requester — one follow-up; then the responsible person
    "follow_up": ("requester", "A follow-up when the requester hasn't replied", "Just checking in",
                  "Hi {first_name}, just checking in on {ref}: we still need:\n\n{missing}\n\nPlease reply to this e-mail."),
    "escalated": ("lead", "To the responsible person: no reply after a follow-up", "No reply from {requester_name} — over to you",
                  "Hi {owner_first},\n\n{ref} — {title} has been waiting on {requester_name} for {days} working days, a follow-up included, and nothing came back. "
                  "You are the responsible person for this ticket.\n\nWhat is still missing:\n{missing}\n\nSummary:\n{summary}\n\nIt is with: {lead}"),
    "lead_escalated": ("lead", "To the person it is with: escalated", "Escalated to {owner_name}",
                       "{ref} — {title} was escalated to {owner_name}: no reply from {requester_name} after a follow-up.\n\nWhat is still missing:\n{missing}"),
}
GREEN = "#0b7a52"


def _fill(text: str, v: dict) -> str:
    """{word} → its value; an unknown {word} stays as typed. Plain words only — never attributes or indexes."""
    return re.sub(r"\{(\w+)\}", lambda m: v[m.group(1)] if m.group(1) in v else m.group(0), text)


def _saved(db) -> dict:
    from .. import state

    try:
        v = json.loads(state.get(db, KEY) or "{}")
    except ValueError:
        v = {}
    return v if isinstance(v, dict) else {}


def get(db, key: str) -> dict:
    who, label, subject, body = DEFAULTS[key]
    mine = _saved(db).get(key) or {}
    return {"key": key, "who": who, "label": label, "subject": mine.get("subject") or subject, "body": mine.get("body") or body,
            "changed": bool(mine), "default_subject": subject, "default_body": body}


def all_(db) -> list[dict]:
    return [get(db, k) for k in DEFAULTS]


def save(db, key: str, subject: str, body: str) -> dict:
    from .. import state

    if key not in DEFAULTS:
        raise KeyError(key)
    subject, body = re.sub(r"\s+", " ", subject or "").strip()[:200], (body or "").replace("\r\n", "\n").strip()[:4000]
    if not subject or len(body) < 5:
        raise ValueError("A subject and a message, please.")
    cur = _saved(db)
    _, _, ds, dbody = DEFAULTS[key]
    if subject == ds and body == dbody:
        cur.pop(key, None)
    else:
        cur[key] = {"subject": subject, "body": body}
    state.set(db, KEY, json.dumps(cur))
    return get(db, key)


def reset(db, key: str) -> dict:
    from .. import state

    cur = _saved(db)
    cur.pop(key, None)
    state.set(db, KEY, json.dumps(cur))
    return get(db, key)


def render(db, key: str, values: dict) -> tuple[str, str]:
    t = get(db, key)
    v = {k: ("" if x is None else str(x)) for k, x in values.items()}
    body, subject = _fill(t["body"], v), _fill(t["subject"], v)
    return re.sub(r"\s+", " ", subject).strip()[:250], re.sub(r"\n{3,}", "\n\n", body).strip()


# ---- dressing a message ------------------------------------------------------------------------------

def _sap_table(rows: list) -> str:
    """1.0.98: the SAP block for the lead — a small two-column table Outlook shows as it is (inline styles)."""
    cells = "".join(f"<tr><td style=\"padding:4px 10px 4px 0;color:#6b7280;white-space:nowrap;vertical-align:top\">{H.escape(str(k))}</td>"
                    f"<td style=\"padding:4px 0;vertical-align:top\">{H.escape(str(v))}</td></tr>" for k, v in rows)
    return (f"<div style=\"font-weight:600;margin:0 0 4px;color:{GREEN}\">SAP</div><table role=\"presentation\" cellspacing=\"0\" cellpadding=\"0\" "
            f"style=\"border-collapse:collapse;border-top:1px solid #e5e7eb;margin:0 0 12px;font-size:13px\">{cells}</table>")


def _paras(text: str, sap_rows: Optional[list] = None) -> str:
    out = []
    for block in re.split(r"\n\s*\n", (text or "").strip()):
        lines = block.split("\n")
        if sap_rows and lines and lines[0].strip() == "SAP" and len(lines) > 1:  # 1.0.98: the SAP block (core.sap_text) as a table
            out.append(_sap_table(sap_rows))
            continue
        if len(lines) > 1 and all(re.match(r"^\s*\d+[.)]\s+", x) for x in lines):
            out.append("<ol style=\"margin:0 0 12px 20px;padding:0\">" + "".join(f"<li style=\"margin:2px 0\">{H.escape(re.sub(r'^\s*\d+[.)]\s+', '', x))}</li>" for x in lines) + "</ol>")
        else:
            out.append("<p style=\"margin:0 0 12px\">" + "<br>".join(H.escape(x) for x in lines) + "</p>")
    return "".join(out)


def build(text: str, extra: Optional[dict] = None) -> tuple[str, Optional[str]]:
    """(plain, html) for a held or outgoing message. extra: heading, link, link_label, note, history[{name, at, text}].
    Without extra the message goes as plain text, as before."""
    plain, html, _ = build_rich(text, extra)
    return plain, html


def build_rich(text: str, extra: Optional[dict] = None) -> tuple[str, Optional[str], bool]:
    """build() and whether a rich message (extra["rich"]: {html, text} — written in the reply editor, its pictures as cid:) was
    put in place of its plain paragraphs. False when it couldn't be found (a changed template, scrubbed words): the caller then
    sends its pictures as ordinary attachments."""
    e = extra or {}
    if not e:
        return text, None, False
    plain = [text.strip()]
    if e.get("link"):
        plain.append(f"{e.get('link_label') or 'Open it in CBX-Connect'}: {e['link']}")
    for b in e.get("buttons") or []:
        plain.append(f"{b['label']}: {b['link']}")
    if e.get("note"):
        plain.append(e["note"])
    hist = e.get("history") or []
    if hist:
        plain.append("— Earlier on this ticket —\n" + "\n\n".join(f"{h.get('name', '')} · {h.get('at', '')}\n{h.get('text', '')}" for h in hist))
    font = "font-family:'Segoe UI',Arial,sans-serif;font-size:14px;line-height:1.5;color:#1f2937"
    parts = [f"<div style=\"{font};max-width:640px\">"]
    if e.get("heading"):
        parts.append(f"<div style=\"border-left:4px solid {GREEN};padding:2px 0 2px 10px;margin:0 0 16px;font-weight:600;color:{GREEN}\">{H.escape(e['heading'])}</div>")
    body_html, placed = _paras(text, e.get("sap_rows")), False
    rich = e.get("rich") if isinstance(e.get("rich"), dict) else None
    if rich and rich.get("html") and (rich.get("text") or "").strip():
        target = _paras(rich["text"])
        if target and target in body_html:  # 1.0.109 (round 2): the editor's message, pictures where they were pasted
            body_html, placed = body_html.replace(target, f"<div style=\"margin:0 0 12px\">{rich['html']}</div>", 1), True
    parts.append(body_html)
    if e.get("link"):
        parts.append(f"<table role=\"presentation\" cellspacing=\"0\" cellpadding=\"0\" style=\"margin:4px 0 14px\"><tr><td style=\"background:{GREEN};border-radius:6px\">"
                     f"<a href=\"{H.escape(e['link'], quote=True)}\" style=\"display:inline-block;padding:9px 18px;color:#ffffff;text-decoration:none;font-weight:600\">{H.escape(e.get('link_label') or 'Open it in CBX-Connect')}</a></td></tr></table>")
    if e.get("buttons"):
        cells = "".join(f"<td style=\"padding-right:8px\"><table role=\"presentation\" cellspacing=\"0\" cellpadding=\"0\"><tr><td style=\"{'background:' + GREEN if b.get('primary') else 'border:1px solid ' + GREEN};border-radius:6px\">"
                        f"<a href=\"{H.escape(b['link'], quote=True)}\" style=\"display:inline-block;padding:9px 16px;color:{'#ffffff' if b.get('primary') else GREEN};text-decoration:none;font-weight:600\">{H.escape(b['label'])}</a></td></tr></table></td>"
                        for b in e["buttons"])
        parts.append(f"<table role=\"presentation\" cellspacing=\"0\" cellpadding=\"0\" style=\"margin:4px 0 14px\"><tr>{cells}</tr></table>")
    if e.get("note"):
        parts.append(f"<p style=\"margin:0 0 12px;color:#6b7280;font-size:12px\">{H.escape(e['note'])}</p>")
    if hist:
        parts.append(f"<div style=\"border-top:1px solid #e5e7eb;margin-top:18px;padding-top:10px;color:#4b5563;font-size:13px\"><div style=\"font-weight:600;margin-bottom:6px\">Earlier on this ticket</div>")
        for h in hist:
            parts.append(f"<div style=\"border-left:3px solid #e5e7eb;padding:2px 0 2px 10px;margin:0 0 10px\"><div style=\"color:#6b7280;font-size:12px\">{H.escape(h.get('name', ''))} · {H.escape(h.get('at', ''))}</div>{_paras(h.get('text', ''))}</div>")
        parts.append("</div>")
    parts.append("</div>")
    return "\n\n".join(plain), "".join(parts), placed
