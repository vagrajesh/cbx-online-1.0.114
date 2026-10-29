"""What C-EXA and her agents may do on their own (1.0.81) — so one owner can run the whole of CBX alone.

Every change they make goes through the app's own API (call_api, and the tools built on it), as the person they
work for. Before the call, this module decides:

    do      routine work — she does it herself, logged: plain users and their details, password resets, tickets,
            support, tool rows, Builder drafts and releases (a release is tested, not live), work areas, PDF Studio,
            harvest, files moved / renamed / filed, folders, To-do, teams, mail drafts and templates …
    ask     waits for the owner's one tap (Approve / Not now) in C-EXA → Jobs & teams → For you: admin or owner
            rights, anyone's access to a tool or a role, disabling people, anything deleted, promote / roll back,
            mail that leaves the building, approving what she learned, business approvals, requests, settings
    never   not hers at all, whoever approves: signing in, passwords and two-step of the person, viewing as
            someone, API keys and connection secrets, the owner's own mailbox

The same decision is made server-side in `require_human` (cbx/security/deps.py) for C-EXA's `cexa-` sessions —
so the line is held by the app, not by her prompt. A tap runs the waiting call as the person who asked, through
a session that is a person's (the owner pressed Approve), so `require_human` lets it through.
"""
from __future__ import annotations

import re
from typing import Any, Optional

DO, ASK, NEVER = "do", "ask", "never"

# (method or "*", path regex, decision, why) — first match wins; anything not listed: do
RULES: tuple[tuple[str, str, str, str], ...] = (
    ("*", r"^/api/auth/", NEVER, "signing in and passwords are the person's own"),
    ("*", r"^/api/view-as", NEVER, "viewing the app as someone else"),
    ("*", r"^/api/mymail(/|$)", NEVER, "the owner's own mailbox has its own agents"),
    ("*", r"^/api/connections(/|$)", NEVER, "API keys are pasted by a person and never seen by the AI"),
    ("*", r"^/api/data-keys(/|$)", NEVER, "a data key is shown once, to a person"),
    ("*", r"^/api/cexa/work/\d+/answer$|^/api/cexa/missions/\d+/(answers|go)$", NEVER, "the owner's own answer or tap — she never approves her own asks"),
    ("*", r"^/api/cexa/goals/\d+/answer$", NEVER, "a goal that waits for a person is the person's to answer (1.0.114)"),
    ("*", r"^/api/voice(/|$)", NEVER, "calls are started and answered by a person"),
    ("*", r"^/api/cexa/missions/\d+/findings/\d+/handoff$", ASK, "turning a finding into work"),
    ("*", r"^/api/users/\d+/mfa-reset$", ASK, "resetting someone's two-step verification"),
    ("*", r"^/api/users/access$", ASK, "giving or taking someone's access to a tool"),
    ("*", r"^/api/users/rules", ASK, "row rules decide who sees what"),
    ("*", r"^/api/roles(/|$)", ASK, "roles decide what people may do"),
    ("*", r"^/api/org/grants", ASK, "access grants"),
    ("*", r"^/api/docwatch/.*access", ASK, "who sees which company"),
    ("*", r"/promote$|/rollback$|/ship$", ASK, "putting a version live"),
    ("*", r"^/api/cexa/proposals/\d+/go$", ASK, "starting a code build"),
    ("*", r"/profiles/.*/(approve|reject)$|/pdfstudio/profiles/purge", ASK, "approving what C-EXA learned"),
    ("*", r"^/api/tickets/.*/publish$", ASK, "publishing a workflow"),
    ("*", r"^/api/requests/\d+/(approve|decline)$", ASK, "deciding a request"),
    ("*", r"^/api/flows/runs/\d+/decide$", ASK, "a business approval waiting for a person"),
    ("*", r"^/api/mailer/(compose|test|settings)$|^/api/mailer/drafts/\d+/send$", ASK, "mail that leaves the building, or how mail goes out"),
    ("*", r"^/api/chat(/|$)", ASK, "a message in the person's own name"),
    ("*", r"^/api/branding", ASK, "the product's name and look"),
    ("*", r"^/api/training/reset$", ASK, "resetting the practice data"),
    ("*", r"^/api/settings$", NEVER, "the person's own settings"),
    ("DELETE", r"^/api/builder/tools/[a-z0-9-]+/draft$", DO, "throwing away her own Builder draft"),
    ("DELETE", r"^/api/builder/tools/[a-z0-9-]+/assets/", DO, "a picture in a Builder draft"),
    ("DELETE", r".", ASK, "deleting"),
)


def _users_rule(method: str, path: str, body: dict) -> Optional[tuple[str, str]]:
    """People: a plain user and their details are routine; rights, tools and disabling wait for the owner."""
    if method == "POST" and path == "/api/users":
        if str(body.get("role") or "user") != "user":
            return ASK, f"a new {body.get('role')}: admin and owner rights wait for the owner"
        if body.get("tools"):
            return ASK, "a new person with access to tools"
        return DO, "adding a plain user"
    if method == "PATCH" and re.match(r"^/api/users/\d+$", path):
        if body.get("role") not in (None, "", "user"):
            return ASK, f"making someone {body.get('role')}"
        if body.get("is_active") is False:
            return ASK, "disabling someone"
        return DO, "changing someone's details"
    if method == "POST" and re.match(r"^/api/users/\d+/temp-password$", path):
        return DO, "a password reset (the new password goes to the person who asked, never to the model)"
    if method == "POST" and path == "/api/users/teams":
        return DO, "a team"
    return None


def _cexa_write(method: str, path: str, body: dict, agent: bool) -> tuple[str, str]:
    """1.0.114: C-EXA's own set-up (her chat, goals, teams, members, work) is deny-by-default for her hands. From an agent's
    work (`agent`) every write here is never — an agent cannot start goals, give work, hire or re-staff teams, or drive
    C-EXA's chat with all her tools. In a person's chat she keeps exactly two hands: make a team (its default lead, operator
    and helper; members of her own choosing wait for the owner) and give a team work for that person."""
    if re.match(r"^/api/cexa/(chat|ask)$", path):
        return NEVER, "C-EXA's chat is a person's to start"
    if re.match(r"^/api/cexa/goals(/|$)", path):
        return NEVER, "goals are started, answered and stopped by a person"
    if agent:
        return NEVER, "an agent never changes C-EXA's teams, work or set-up"
    if method == "POST" and path == "/api/cexa/teams":
        return (ASK, "a team with members of C-EXA's choosing") if body.get("members") else (DO, "making a team (a lead, an operator and a helper)")
    if method == "POST" and path == "/api/cexa/work":
        return DO, "giving a team work, for the person C-EXA is helping"
    if re.match(r"^/api/cexa/teams/[^/]+(/members(/|$)|$)", path):
        return ASK, "who is on a team, what it may do, and switching it on or off"
    return NEVER, "C-EXA's own set-up is a person's"


def decide(method: str, path: str, body: Any = None, *, agent: bool = False) -> tuple[str, str]:
    """(do | ask | never, why) for one call of the app's API by C-EXA. `agent`: the call comes from an agent's work
    (cbx/cexa/loop.py), not from a person's chat with her (1.0.114)."""
    method = (method or "GET").upper()
    path = (path or "").split("?")[0].rstrip("/") or "/"
    if not path.startswith("/api/") or ".." in path:
        return NEVER, "only the app's own /api addresses"
    if method in ("GET", "HEAD", "OPTIONS"):
        if re.match(r"^/api/(mymail|connections/\d+/secret|data-keys)", path):
            return NEVER, "the owner's mailbox and keys"
        return DO, "looking"
    b = body if isinstance(body, dict) else {}
    if path.startswith("/api/users"):
        hit = _users_rule(method, path, b)
        if hit:
            return hit
    for m, pat, verdict, why in RULES:
        if (m == "*" or m == method) and re.search(pat, path):
            if agent and verdict != NEVER and path.startswith("/api/cexa"):
                break  # 1.0.114: from an agent's work C-EXA's own set-up is never (below)
            return verdict, why
    if path.startswith("/api/cexa"):
        return _cexa_write(method, path, b, agent)
    if path.startswith("/api/users"):
        return ASK, "people and access"
    return DO, "routine work"


SECRET_KEYS = re.compile(r"(password|secret|token(?!_limit|s$)|recovery|api_?key|client_?key|otp)", re.I)
CODE_KEY = re.compile(r"^(code|pin)$", re.I)
CODE_VALUE = re.compile(r"^\d{4,8}$")


def _secret(k: str, v: Any) -> bool:
    """A password, key, token or one-time code — not a Builder column's `key`, a count of tokens or a site code."""
    if not isinstance(v, str) or not v:
        return False
    return bool(SECRET_KEYS.search(k)) or (bool(CODE_KEY.match(k)) and bool(CODE_VALUE.match(v.strip())))


def redact(obj: Any, found: Optional[list] = None) -> Any:
    """The API's answer as the model may read it: every password, key or code taken out (and collected, so it can
    be handed to the person who asked)."""
    if found is None:
        found = []
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _secret(str(k), v):
                found.append((str(k), str(v)))
                out[k] = "[given to the person, not to C-EXA]"
            else:
                out[k] = redact(v, found)
        return out
    if isinstance(obj, list):
        return [redact(x, found) for x in obj]
    return obj
