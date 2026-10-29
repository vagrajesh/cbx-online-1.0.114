"""Which lessons exist and who they are for.

Kept on the server so the Training page can be filtered by what THIS person actually holds: a
lesson about Entity Compliance is not offered to someone without that tool, and the admin lessons
are not offered to a user. The steps themselves — what to highlight, what to say, what to wait for
— live next to the screens they teach, in `web/training/lessons.js`, keyed by the same ids.
`tests/test_training.py` checks the two lists agree, so a lesson cannot exist on one side only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..models import User


@dataclass(frozen=True)
class Lesson:
    id: str
    title: str
    blurb: str                       # one sentence: what you will be able to do afterwards
    minutes: int
    group: str                       # the heading it sits under on the Training page
    tool: Optional[str] = None       # requires this tool (slug) — None = everyone
    cap: Optional[str] = None        # requires this capability — None = everyone
    needs_email: bool = False        # the lesson sends something; skipped if mail is off
    tags: tuple[str, ...] = field(default_factory=tuple)


LESSONS: list[Lesson] = [
    # -- everyone ------------------------------------------------------------------------------
    Lesson("around.sidebar", "Getting around", "Know what every part of the screen is for and where your own things live.", 3, "Start here"),
    Lesson("around.ticket", "Report a problem", "Open a ticket for CBX-Connect, Chase or another system, answer C-EXA's questions, and follow it to done.", 3, "Start here"),
    Lesson("around.request", "Ask for something to be built", "Send a request, follow it in My requests, and answer C-EXA's questions in Messages.", 5, "Start here", cap="admin.all"),
    Lesson("around.settings", "Your settings and your password", "Change your name and password, and sign out other devices.", 3, "Start here"),

    # -- PDF Studio ----------------------------------------------------------------------------
    Lesson("pdf.batch", "Read a batch of PDFs", "Pick a template, drop in the bills, and get a filled sheet back.", 8, "PDF Studio", tool="pdf-studio"),
    Lesson("pdf.results", "Check and fix what C-EXA read", "Find the cells that need you, fill them in, and send the sheet to a master sheet.", 5, "PDF Studio", tool="pdf-studio"),

    # -- Master Sheets -------------------------------------------------------------------------
    Lesson("sheets.new", "Start a master sheet", "Create a sheet with the columns you need, ready to be fed from PDF Studio.", 4, "Master Sheets", tool="master-sheets"),

    # -- for admins and managers ---------------------------------------------------------------
    Lesson("admin.people", "Add a person and give them tools", "Create an account, hand over the temporary password, and grant exactly the tools they need.", 5, "Managing people", cap="people.add"),
    Lesson("admin.roles", "Make a role of your own", "Copy a built-in role, change what it can do, and understand why some boxes are greyed out.", 5, "Managing people", cap="people.roles"),
    Lesson("admin.audit", "Read the audit trail", "Find who did what, when, and see the entries C-EXA wrote about her own work.", 3, "Managing people", cap="audit.view"),
    Lesson("owner.versions", "Put a new version live", "Upload a package, run it in a sandbox, promote it, and roll back if you have to.", 6, "Owner", cap="versions.promote"),
]

BY_ID = {l.id: l for l in LESSONS}


def available(me: User, tool_slugs: set[str]) -> list[tuple[Lesson, bool, str]]:
    """Every lesson with (lesson, can_take, why_not) for this person. Unavailable lessons are still
    returned — the page shows them greyed with the reason, so a person knows the tool exists and
    who to ask — but only when the reason is a tool grant. Capability-gated lessons someone cannot
    hold are simply not theirs to see."""
    out = []
    for l in LESSONS:
        if l.cap and not me.can(l.cap):
            continue
        if l.tool and l.tool not in tool_slugs and not me.is_admin:
            out.append((l, False, f"You don't have the {TOOL_NAMES.get(l.tool, l.tool)} tool yet. Ask your manager or an admin to grant it."))
            continue
        out.append((l, True, ""))
    return out


TOOL_NAMES = {"doc-watch": "Entity Compliance", "pdf-studio": "PDF Studio", "todo": "To-do", "master-sheets": "Master Sheets", "ifrs16": "IFRS 16", "site-schedules": "Site Schedules"}
