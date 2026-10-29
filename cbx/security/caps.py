"""What a role may do, as a named list.

Until now a role was a string and every check asked "is this string 'owner' or 'admin'?" — 152
checks across 25 files. This module does not rewrite any of them. It puts a capability set behind
the string, and `User.is_admin` / `is_manager` / `is_owner` now answer from that set. Every existing
check keeps working, unchanged, and a role you invent yourself lights up the same code paths a
built-in does.

The rule that makes this safe: **a person can never grant a capability they do not hold, and can
never remove one from themselves.** Without it, an editable permission system is a single click away
from an app nobody can administer — which is the same trap as the owner lockout in 1.0.26, only
harder to undo because there is no `unlock-owner.bat` for "the owner role no longer manages users".
"""
from __future__ import annotations

from typing import Iterable

# key → (group, label, what it means in one line a non-technical owner can act on)
CAPS: dict[str, tuple[str, str, str]] = {
    # -- the whole building ---------------------------------------------------
    "everything": ("Everything", "Everything", "No limits. Only the owner role should hold this."),
    "admin.all": ("Everything", "Treated as an admin", "Opens every admin page and admin-only data. Coarse on purpose: the app has 152 checks that ask only this."),
    # -- people ---------------------------------------------------------------
    "people.view": ("People", "See people", "Open Users & roles and see who is on the system."),
    "people.add": ("People", "Add and edit people", "Create accounts, rename, disable, issue temporary passwords."),
    "people.roles": ("People", "Change roles", "Give someone a different role. Never above your own."),
    "team.scope": ("People", "Their own team only", "Narrows every capability below to their own team. A limit, not a grant — so anyone building a role may add it."),
    # R8: "limited to a team" and "manages the team" used to be the same flag, so anything team-limited (a
    # viewer a manager made) also got a manager's powers. This is the manager half, on its own.
    "team.lead": ("People", "Leads their team", "A manager's powers over their own team: the team's rows in tools, its companies' people, its approvals, chat rooms. Only with 'Their own team only'."),
    # -- the app itself -------------------------------------------------------
    "code.propose": ("Building", "Propose code changes", "Write a change and send it to the board. Cannot promote it."),
    "code.review": ("Building", "Review the board", "Move cards, accept and reject what C-EXA proposes."),
    "versions.promote": ("Building", "Promote a version", "Put new code on the live server. The highest-risk one."),
    "versions.sandbox": ("Building", "Run a sandbox", "Start and stop a staged version to click through it."),
    # -- the tools ------------------------------------------------------------
    "tools.grant": ("Tools", "Grant tools", "Decide who can open which tool."),
    "entities.manage": ("Entity Compliance", "Manage entities", "Add, edit and archive entities and their columns."),
    "entities.delete": ("Entity Compliance", "Delete entities", "Remove an entity outright, not archive it."),
    "profiles.approve": ("PDF Studio", "Approve what C-EXA learned", "Accept a new profile version as live."),
    "profiles.delete": ("PDF Studio", "Delete profiles", "Remove a learned layout and its versions."),
    "batches.rerun": ("PDF Studio", "Re-run batches", "Read a batch again with the current rules."),
    # -- oversight ------------------------------------------------------------
    "audit.view": ("Oversight", "See the audit trail", "Every action by every person and by C-EXA."),
    "org.view": ("Oversight", "See the org chart", "Who reports to whom, and who can see whose work."),
    "org.edit": ("Oversight", "Change the org chart", "Move people, draw and remove access wires."),
    "teams.view": ("Oversight", "See C-EXA's teams", "The Agents page, read-only: every team, its lead and members, what they are working on and what they found."),
}

GROUPS = ["Everything", "People", "Building", "Tools", "Entity Compliance", "PDF Studio", "Oversight"]

# The four that already exist. Their capability sets are chosen to reproduce today's behaviour
# EXACTLY — `tests/test_roles.py` asserts that, person by person and endpoint by endpoint.
# `locked` means the role cannot be deleted and its capabilities cannot be edited.
BUILTIN: dict[str, dict] = {
    "owner": {
        "name": "Owner",
        "description": "Everything, including promoting new code and making other owners.",
        "rank": 100,
        "locked": True,
        "caps": ["everything"],
    },
    "admin": {
        "name": "Admin",
        "description": "Builds for everyone: requests, workflows, the warehouse, people and tools.",
        "rank": 70,
        "locked": True,
        "caps": ["admin.all", "people.view", "people.add", "people.roles", "team.lead", "tools.grant", "code.propose",
                 "code.review", "versions.sandbox", "entities.manage", "entities.delete",
                 "profiles.approve", "batches.rerun", "audit.view", "org.view", "org.edit"],
    },
    "manager": {
        "name": "Manager",
        "description": "Oversees one team. Sees and manages their own people, and needs tool grants like anyone else.",
        "rank": 40,
        "locked": True,
        "caps": ["people.view", "people.add", "people.roles", "team.scope", "team.lead", "audit.view",
                 "entities.manage", "org.view"],
    },
    "user": {
        "name": "User",
        "description": "Their tools, their requests, their results.",
        "rank": 10,
        "locked": True,
        "caps": [],
    },
}

# Shipped ready-made, and editable — these are the three Edwin asked for. They are presets, not
# built-ins: change them, rename them, delete them.
PRESETS: dict[str, dict] = {
    "developer": {
        "name": "Developer admin",
        "description": "Builds and tests the app. Cannot touch people or promote to production.",
        "rank": 60,
        "locked": False,
        "caps": ["code.propose", "code.review", "versions.sandbox", "batches.rerun", "profiles.approve",
                 "audit.view", "org.view"],
    },
    "user_admin": {
        "name": "User admin",
        "description": "Looks after people, accounts and access. Cannot change the app itself.",
        "rank": 60,
        "locked": False,
        "caps": ["people.view", "people.add", "people.roles", "tools.grant", "audit.view", "org.view", "org.edit"],
    },
    "viewer": {
        "name": "Viewer",
        "description": "Can look, and change nothing at all.",
        "rank": 5,
        "locked": False,
        "caps": ["people.view", "audit.view", "org.view", "teams.view"],
    },
}


def valid(caps: Iterable[str]) -> list[str]:
    """Keep only capabilities that exist. An unknown key in the database must never widen access."""
    return [c for c in dict.fromkeys(caps) if c in CAPS]


# A limit, not a grant. "Everything" must never hand these out, or the owner would come back as a
# manager scoped to one team — which is exactly what the first version of this file did.
NARROWING = {"team.scope"}


def expand(caps: Iterable[str]) -> set[str]:
    """`everything` means every capability except the narrowing ones — so a capability added in a
    later release belongs to the owner immediately rather than silently to nobody."""
    s = set(valid(caps))
    if "everything" in s:
        return set(CAPS) - NARROWING
    return s


def describe(caps: Iterable[str]) -> list[dict]:
    """For the roles screen: the capability list, grouped, with what each one means.

    `narrowing` tells the screen not to grey the box out. The owner does not "hold" `team.scope` —
    by design, or `everything` would scope him to one team — but he must still be able to put that
    limit on a role he builds, or nobody could ever create a team-scoped role again."""
    held = expand(caps)
    out = []
    for key, (group, label, meaning) in CAPS.items():
        out.append({"key": key, "group": group, "label": label, "meaning": meaning,
                    "held": key in held, "narrowing": key in NARROWING})
    return out
