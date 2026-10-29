"""C-EXA as CEO (1.0.49). The owner gives her a job; she runs a team to do it.

    brief ──▶ her questions ──▶ the owner's answers ──▶ her plan ──▶ the owner says Go
          ──▶ the team works the tasks (developers, designer, tester, helper, core developer)
          ──▶ her write-up (review) ──▶ the owner opens it in the Builder, tests it, releases it

What the team builds:
  * a tool folder, as a **Builder draft** (DATA_DIR/builder/tools/<slug>/) — data tables, pages, flows,
    the four languages, a README. Nothing is live until the owner releases it from the Builder and
    promotes the version with the notice, like any other version. Flows the team writes are switched off.
  * core-code changes, as proposals built in a **sandbox copy** of the app (cbx/cexa/developer.py): the
    tests must pass there, and the owner ships the diff from "C-EXA as developer". Never the live tree.

What the team never does: release, promote, grant access, sign in as anyone, use API connections, write
code or embed blocks into a page, or see a real row — the designer's screenshots are of made-up rows.

Every step is an event (cexa_events) with the prompt and the reply behind it, so the owner can see
everything: who is on what, what they wrote, what the check said, what the designer saw.

The runner is one thread per job (one GPU: the model answers one agent at a time; the page shows who is
waiting). A routine picks a job up again after a restart. In tests (jobs inline) a job runs to its end in
the request that says Go.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from .. import jobs
from ..audit import log
from ..branding import ai_name, product_name
from ..config import settings
from ..db import SessionLocal, utcnow
from ..models import CexaMessage, Mission, MissionAgent, MissionEvent, MissionTask, Proposal, User
from .gateway import BudgetExhausted, Gateway, ModelError

# ---- the team ---------------------------------------------------------------------------------------

ROLE_INFO = {
    "lead": ("Lead", "Asks what she needs to know, writes the plan, hands out the tasks, checks the work, writes it up."),
    "developer": ("Developer", "Builds the tool as a Builder draft: data tables, pages, flows."),
    "core": ("Core developer", "Changes CBX-Connect's own code in a sandbox copy. The tests must pass; you ship it."),
    "designer": ("Designer", "Looks at every page (a screenshot with made-up rows) against CBX-Connect's look and asks for fixes."),
    "tester": ("Tester", "Checks the draft loads, every page draws, every flow runs dry, every word is there."),
    "helper": ("Helper", "The small jobs: made-up rows for the screenshots, the translations, the README."),
}
ROLE_ORDER = ("lead", "developer", "core", "designer", "tester", "helper")
TEAM_BLOCKS = ("heading", "text", "metrics", "button", "stats", "table", "chart", "board", "form", "links", "divider", "spacer")
MAX_ATTEMPTS = 3
LOOK_PASS = 75  # the designer's score a page must reach; below it she asks for one round of fixes
TEAM_ICONS = ("tool", "shield", "pdf", "doc", "sheet", "tower", "ledger", "list", "check", "inbox", "board", "users", "flow", "warehouse", "clock", "grid", "key", "mail", "box", "blocks")


def model_for_role(role: str) -> str:
    from .models_fit import pick  # 1.0.62: what the owner picked on Agents → Now → Models wins

    tier = {"developer": "worker", "core": "worker", "designer": "eyes", "helper": "light"}.get(role)
    return (pick(tier) if tier else "") or {"developer": settings.model_developer, "core": settings.model_developer, "designer": settings.model_designer, "helper": settings.model_helper}.get(role) or settings.cexa_model


class OutOfCalls(Exception):
    pass


class Stopped(Exception):
    pass


# ---- what the agents are told -------------------------------------------------------------------------

SPEC_DATA = """A data table is one JSON object: {"columns": [{"key": "name", "type": "text", "label": "Name", "required": true}], "write": "everyone", "scope": "all"}
- key: lowercase_with_underscores. Never id, created_at or created_by (every table has those already).
- type: text, int, number, date (YYYY-MM-DD), bool, choice, link, file, person, checklist or calc.
  - choice: a pick-list — {"key": "status", "type": "choice", "label": "Status", "options": ["Open", "In progress", "Done"]}. A status, stage, priority or category is a choice (2 to 8 options, a word or two each).
  - link: a row of another table of this tool — {"key": "project", "type": "link", "label": "Project", "table": "projects", "show": "name"}. Never type another table's name or id into a text column; link to it.
  - file: one document or picture per row — {"key": "evidence", "type": "file", "label": "Evidence", "accept": ["pdf", "png", "jpg", "docx", "xlsx"], "max_mb": 10}.
  - person: someone in CBX-Connect — {"key": "who", "type": "person", "label": "Who", "who": "team"} ("team": only people of their own team; leave it out for everyone).
  - checklist: a list of steps with ticks. calc: worked out, never typed — {"calc": {"fn": "count"|"done", "of": "<checklist>"}}, {"calc": {"fn": "days", "from": "<date>"|"created", "to": "<date>"|"today"}} or {"calc": {"fn": "grade", "of": "<number column>", "grades": [[3, "Small"], [7, "Medium"], [null, "Large"]]}}.
  - default (optional): what a new row starts with — a choice's option, "today" for a date, "me" for a person.
- label: the words people see, plain English, 1 to 3 words.
- write: everyone (anyone who may use the tool adds rows), managers (managers and admins), admins, or nobody.
- scope: all (everyone sees every row), mine (their own rows and rows naming them; a manager also their team's) or team (each team its own).
  With mine, "share": {"field": "<a choice column>", "value": "<one option>"} lets the whole team see those rows (e.g. a Kind of Team).
- unique (optional): [["goal", "person"]] — these columns together only once.
Keep it small: 3 to 8 columns, only what the job needs."""

SPEC_PAGE = """A page is one JSON object: {"title": "Projects", "intro": "One line saying what the page is for.", "width": "normal", "sections": [{"columns": [{"width": 12, "blocks": [ ... ]}]}]}
Columns in one section add up to 12 (12 = full width; 8 + 4 = main and side). Blocks, top to bottom:
- {"id": "intro", "type": "text", "text": "A short paragraph. **bold** and - lists work."}
- {"id": "counts", "type": "metrics", "items": [{"metric": "<a key from NUMBERS>"}]}   (the server's numbers only)
- {"id": "list", "type": "table", "title": "Projects", "source": {"data": "<table>"}, "columns": [{"key": "<column>"}], "search": true, "filters": ["<a choice, link or bool column>"],
   "where": [{"field": "<column>", "op": "<op>", "value": "..."}], "sort": {"key": "<column>", "desc": false}}   (where and sort are optional)
- {"id": "open", "type": "table", "title": "Open", "show": "totals", "totals": ["<a number column>"], "source": {...}}   (the row count and sums, as tiles)
- {"id": "bars", "type": "chart", "title": "Per status", "source": {...}, "group": "<column>", "value": {"fn": "count"}}   (or {"fn": "sum", "of": "<number column>"})
- a source is {"data": "<table>"} (the tool's own), {"sheet": "<a warehouse master sheet>"} (read only), or {"provider": "<provider>"}
- {"id": "core", "type": "table", "title": "...", "source": {"provider": "<a table PROVIDER>"}}
- {"id": "board", "type": "board", "title": "Board", "source": {"data": "<table>"}, "lane": "<a choice column>", "card": "<text column>", "fields": ["<column>"]}   (drag a card to change its lane)
- a table or board of the tool's own may have "actions": [{"label": "Take it", "flow": "<a flow whose trigger is {type: button, data: <that table>}>"}] (a button on each row; the flow gets {{trigger.row}})
  and "preset": {"<choice column>": "<option>"} (what a row added from that block starts with)
- {"id": "add", "type": "form", "title": "Add a project", "source": {"data": "<table>"}, "fields": [{"key": "<column>"}], "submit": "Add"}
- {"id": "run", "type": "button", "flow": "<a flow whose trigger is button>", "label": "Send the reminders", "confirm": "Send them now?"}
- {"id": "go", "type": "links", "kind": "buttons", "items": [{"label": "Open the tasks", "route": "tool", "params": {"t": "<slug>", "p": "<page>"}}]}
- {"id": "h1", "type": "heading", "text": "A heading", "level": 2}   {"id": "d1", "type": "divider"}   {"id": "sp", "type": "spacer", "height": 16}
Words are plain English — the team files them under language keys for you. Block ids: lowercase letters, digits and _, unique."""

BUILDER_GUIDE = """How the Builder works — what the owner sees and changes after the team is done, so build only what it can show:
- A tool = pages + data (its own tables) + rules (flows) + words in English, Arabic, Portuguese and French. One folder; the Builder edits it on one screen.
- A page = rows of 1–3 columns; each column holds parts top to bottom: table, chart, numbers, compliance grid, heading, text, picture, links, form, button, line, space (website and code are the owner's, never the team's).
- A table or chart takes its rows from: this tool's table, a warehouse master sheet (read only), or another tool's data (a provider) — then "only rows where …", sorted by, every row or totals.
- Own tables: text, number, whole number, date, yes/no, pick-list (choice + options), a row of another table (link), a file, a person, a checklist, a worked-out column. Statuses are pick-lists.
- Who sees rows: all · each person their own (optionally shared with the team where a pick-list has one value) · each team its own · the members of a linked row (the owner sets that one). Who adds: everyone · managers · admins · nobody (rules only).
- Parts for work: a board (cards in lanes of a pick-list), buttons on each row that start a rule, progress bars (the owner sets those).
- A rule = WHEN (button · a row added · a row changed · a schedule) → ROWS (optional: find rows, then for each) → IF (only "yes") → THEN (tell, email, approval, change the row, add a row, wait, stop) → AFTER (once at the end). Nothing else: the owner must be able to open it.
- Pages can be for some roles only (access.roles). New rules are switched off. Nothing goes live without the owner's release and promote."""

SPEC_FLOW = """A flow is one JSON object: {"title": "Daily follow-up", "trigger": {...}, "run_as": "owner", "start": "<first step id>", "steps": {"<id>": {"type": "...", ..., "next": "<id>"}}}
Triggers: {"type": "button"} (a Run button on a page starts it) · {"type": "row_added", "data": "<table>"} · {"type": "row_changed", "data": "<table>"} · {"type": "schedule", "every": "hour|day|weekday|week|month", "at": "07:00"} (a schedule runs as "owner").
run_as: "starter" (whoever started it) or "owner".
Steps (each has an id; "next" is the step after it; the last step has no next):
- find_rows: {"data": "<table>"} or {"provider": "<provider>"} or {"sheet": "<a warehouse master sheet>"}, "where": [{"field": "<column>", "op": "<op>", "value": "..."}], "as": "items", "limit": 200
- if: "when": [{"left": "{{item.status}}", "op": "equals", "value": "late"}], "match": "all|any", "yes": "<id>", "no": "<id>"
- for_each: "items": "items", "as": "item", "body": "<step run for every item>", "next": "<after all of them>"
- add_row / update_row: "data": "<table>", "values": {"<column>": "{{...}}"}, "row": "{{item.id}}" (update only)
- set: "name": "count", "value": "{{items_count}}"
- notify: "who": {"kind": "starter|owner|roles|people", "roles": ["admin"], "emails": []}, "title": "...", "text": "..."
- email: "to": "{{item.owner_email}}", "subject": "...", "text": "..."
- approval: "who": {...}, "question": "...", "yes": "<id>", "no": "<id>"
- wait: "days": 1 (or "hours", "minutes") · stop: "outcome": "done|failed", "message": "..."
- require: "when": [{"left": "{{trigger.row.checks_done}}", "op": "gte", "value": "{{trigger.row.checks_n}}"}], "message": "Tick every line first." — stops the rule, failed, with that sentence, unless it holds
Ops: equals, not_equals, gt, gte, lt, lte, contains, not_contains, empty, not_empty, days_from_today, within_days, past.
Templates: {{today}}, {{person.name}}, {{trigger.row.<column>}}, {{<as>.<column>}}, {{<as>_count}}, {{approval.note}}.
No API steps. Messages and emails are plain English sentences.
Keep to the Builder's rule shape, so the owner can open and change it without code:
  [find_rows → for_each (body: if steps with only "yes" → the action steps; next: the steps once at the end)]
  or [if steps with only "yes" → the action steps]. An If never has a "no"; an approval's "yes" is the next step and it has no "no"."""

DESIGN_RULES = """CBX-Connect's look — every page must feel like the rest of the app:
- Less is more. A page does one job. At most 6 blocks; nothing decorative.
- The page's title and one-line intro are drawn at the top already; do not repeat them in a heading block.
- Numbers first when there are counts that matter, then the main table, then forms.
- A form that adds rows sits beside its table (table 8 wide, form 4 wide) or on its own page — never above the table.
- Plain, short words: sentence case, no jargon, no exclamation marks, no emoji. Labels of 1 to 3 words.
- Colours, fonts and spacing come from the app: white cards on a light page, dark green (#003F2D) as the accent, orange only for warnings, red only for problems. Never invent colours.
- Tables show 3 to 6 columns — the ones a person scans. Search on for lists that grow.
- One home page; every page reachable from the tool's tabs."""


# ---- small helpers ------------------------------------------------------------------------------------

_SLUG_RX = re.compile(r"[^a-z0-9]+")


def _slug(s: str, n: int = 32) -> str:
    v = _SLUG_RX.sub("-", (s or "").lower()).strip("-")
    if len(v) > n:
        v = v[:n].rsplit("-", 1)[0] if "-" in v[:n] else v[:n]
    return v.strip("-") or "new-tool"


def _name(s: str, n: int = 40) -> str:
    v = re.sub(r"[^a-z0-9_]+", "_", str(s or "").lower()).strip("_")[:n]
    return v if v and v[0].isalpha() else ("x_" + v if v else "")


def _text(v: Any, n: int = 400) -> str:
    return (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) if v is not None else "").strip()[:n]


def _owner(db: Session, m: Mission) -> Optional[User]:
    return db.get(User, m.created_by_id) if m.created_by_id else None


def _cexa(db: Session) -> User:
    from .brain import cexa_user

    return cexa_user(db)


def say(db: Session, m: Mission, text: str, *, agent: Optional[MissionAgent] = None, task: Optional[MissionTask] = None, kind: str = "said", detail: Optional[dict] = None) -> MissionEvent:
    ev = MissionEvent(mission_id=m.id, agent_id=agent.id if agent else None, task_id=task.id if task else None, kind=kind, text=text[:2000], detail=detail or {})
    db.add(ev)
    db.flush()
    return ev


def _lead(db: Session, m: Mission) -> MissionAgent:
    a = db.query(MissionAgent).filter(MissionAgent.mission_id == m.id, MissionAgent.role == "lead").first()
    if a is None:
        a = MissionAgent(mission_id=m.id, name=ai_name(), role="lead", focus="the plan and the team", model=model_for_role("lead"), status="working")
        db.add(a)
        db.flush()
    return a


def _notify(db: Session, m: Mission, title: str, text: str) -> None:
    o = _owner(db, m)
    if o is not None:
        db.add(CexaMessage(user_id=o.id, from_axl=True, kind="needs_you", title=title, text=text[:1000], link=f"#/agents?job={m.id}", meta={"job": m.id}))


def _set(agent: Optional[MissionAgent], status: str, doing: str = "", task: Optional[MissionTask] = None) -> None:
    if agent is None:
        return
    agent.status = status
    agent.doing = doing[:300]
    agent.task_id = task.id if task else None
    agent.updated_at = utcnow()


def _system(agent: MissionAgent, extra: str = "") -> str:
    """Short system prompts: the context window is shared with the spec and the draft."""
    if agent.role == "lead":
        from .persona import persona

        return persona("You are running a job for the owner as the head of a small team of agents. You plan the work and check it. " + extra)
    from . import teams as T

    label, what = ROLE_INFO.get(agent.role) or T.ROLES.get(agent.role) or (("Team lead", "Runs the team: hands out the work, checks it, sums it up.") if agent.role == "teamlead" else ("Agent", ""))
    return (f"You are {agent.name}, the {label.lower()} on {ai_name()}'s team, building tools for {product_name()} at CBRE Excellerate "
            f"for non-technical colleagues. {what} You never guess: work only from what you are given. {extra}").strip()


_GPU = threading.Lock()

REQUIRED = {"ceo_change": ["changes"], "team_analyse": ["findings"], "team_check": [], "team_write": ["report"], "team_outline": ["sections"], "team_draft": ["markdown"],
            "team_acheck": [], "team_final": ["markdown"], "team_lens": ["findings"], "team_debate": [], "team_verdict": [],
            "ceo_intake": ["understood"], "ceo_plan": ["pages"], "ceo_revise": ["changes"], "ceo_summary": ["summary"], "team_data": ["columns"], "team_flow": ["steps"],
            "team_page": ["sections"], "team_fix": ["sections"], "team_look": ["score"], "team_words": ["words"], "team_readme": ["readme"], "team_sample": []}


def _ask(db: Session, m: Mission, agent: MissionAgent, task: Optional[MissionTask], name: str, instruction: str, payload: dict, schema: dict, *, images: Optional[list[bytes]] = None, system_extra: str = "") -> dict:
    """One model call for one agent: the instruction, the INPUT block (JSON), the reply schema. Counted
    against the job's budget and kept as an event with the prompt and the reply."""
    db.refresh(m, ["status", "paused"])
    if m.status in ("stopped",):
        raise Stopped()
    if m.calls >= m.max_calls:
        raise OutOfCalls()
    system = _system(agent, system_extra)
    user = instruction.strip() + "\n\nINPUT:\n" + json.dumps(payload, ensure_ascii=False, indent=1) + "\nEND INPUT"
    t0 = time.time()
    err, out = "", {}
    # what the job wrote so far is saved before the model is asked: a model call takes up to minutes, and
    # on SQLite an open write is the database's one write lock — everyone else waits behind it (1.0.60)
    db.commit()
    try:
        with _GPU:  # one GPU: the teams' model calls go one at a time, so none times out waiting in Ollama's queue
            t0 = time.time()
            out = Gateway(db).json(task=name, system=system, user=user, schema=schema, images=images, required=REQUIRED.get(name, []))
    except ModelError as e:
        err = str(e)
        out = {}
        raw = getattr(e, "raw", "")
        say(db, m, f"The model did not give {agent.name} a usable answer ({err[:160]}).", agent=agent, task=task, kind="problem", detail={"system": system[:6000], "user": user[:12000], "reply": raw[:8000]})
        raise
    finally:
        m.calls += 1
        agent.calls += 1
    if not err:
        say(db, m, f"{agent.name} asked the model ({model_for_role(agent.role)}, {time.time() - t0:.0f} s).", agent=agent, task=task, kind="model",
            detail={"system": system[:6000], "user": user[:12000], "reply": json.dumps(out, ensure_ascii=False, indent=1)[:12000], "images": len(images or [])})
    return out


# ---- the draft -----------------------------------------------------------------------------------------


def draft_dir(slug: str) -> Path:
    from ..api.builder_api import drafts_root

    return drafts_root() / slug


_DRAFT_LOCK = threading.RLock()  # the runner writes the draft while the page reads it


def _read(m: Mission) -> dict:
    with _DRAFT_LOCK:
        return _read_unlocked(m)


def _read_unlocked(m: Mission) -> dict:
    from ..engine import loader

    d = draft_dir(m.tool_slug)
    if not (d / "tool.json").exists():
        return {"manifest": {}, "pages": {}, "data": {}, "flows": {}, "languages": {"en": {}}, "readme": ""}
    t = loader.read_folder(d)
    return {"manifest": dict(t.manifest), "pages": dict(t.pages), "data": dict(t.data), "flows": dict(t.flows), "languages": {k: dict(v) for k, v in t.languages.items()}, "readme": t.readme}


def _write(db: Session, m: Mission, F: dict, *, agent: Optional[MissionAgent] = None, what: str = ""):
    """Put the draft down (words filed under keys first) and read it back: the loader's problems are the check."""
    from ..api.builder_api import write_folder
    from ..engine import loader

    keyify(F)
    d = draft_dir(m.tool_slug)
    with _DRAFT_LOCK:
        d.mkdir(parents=True, exist_ok=True)
        write_folder(d, F["manifest"], F["pages"], F["data"], F["languages"], F.get("readme") or "", F["flows"])
        (d / ".cexa-job").write_text(json.dumps({"job": m.id, "title": m.title, "round": m.round}), encoding="utf-8")
        t = loader.read_folder(d)
    log(db, "cexa_draft_written", actor=_cexa(db), actor_label=f"axl→agent:{agent.name if agent else 'team'}", target=m.tool_slug, detail=f"job {m.id}: {what}"[:300])
    return t


_TEXT_FIELDS = ("title", "intro", "text", "label", "confirm", "submit", "alt", "caption")


def keyify(F: dict) -> None:
    """Agents write plain words; the engine files them under language keys (the Builder's own naming:
    tool.title, page.<name>.title, w.<block>.<field>, col.<table>.<column>, flow.<name>.title). A value
    that is already a key the English file has is left alone."""
    from ..engine.loader import KEY_RE

    en = F["languages"].setdefault("en", {})

    def k(key: str, v: Any) -> Any:
        if not isinstance(v, str) or not v.strip():
            return v
        if KEY_RE.match(v) and v in en:
            return v
        en[key] = v.strip()
        return key

    man = F["manifest"]
    if man.get("title"):
        man["title"] = k("tool.title", man["title"])
    for tname, b in F["data"].items():
        for c in b.get("columns") or []:
            if isinstance(c, dict) and c.get("label"):
                c["label"] = k(f"col.{tname}.{c.get('key')}", c["label"])
    for fname, f in F["flows"].items():
        if f.get("title"):
            f["title"] = k(f"flow.{fname}.title", f["title"])
    for pname, p in F["pages"].items():
        for fld in ("title", "intro"):
            if p.get(fld):
                p[fld] = k(f"page.{pname}.{fld}", p[fld])
        for sec in ((p.get("layout") or {}).get("sections") or []):
            if sec.get("title"):
                sec["title"] = k(f"page.{pname}.{sec.get('id', 's')}.title", sec["title"])
            for col in sec.get("columns") or []:
                for w in col.get("widgets") or []:
                    wid = str(w.get("id") or "")
                    for fld in _TEXT_FIELDS:
                        if w.get(fld):
                            w[fld] = k(f"w.{wid}.{fld}", w[fld])
                    for c in w.get("columns") or []:
                        if isinstance(c, dict) and c.get("label"):
                            c["label"] = k(f"w.{wid}.col.{c.get('key')}", c["label"])
                    for c in w.get("fields") or []:
                        if isinstance(c, dict) and c.get("label"):
                            c["label"] = k(f"w.{wid}.f.{c.get('key')}", c["label"])
                    for i, it in enumerate(w.get("items") or []):
                        if isinstance(it, dict) and it.get("label"):
                            it["label"] = k(f"w.{wid}.i{i}", it["label"])


def problems_for(errors: list, prefix: str) -> list[str]:
    return [e for e in errors if e.startswith(prefix)]


def _strings(F: dict) -> dict:
    return dict(F["languages"].get("en") or {})


def _say_words(F: dict, v: Any) -> Any:
    """A key back to its English, for showing a part to an agent."""
    en = _strings(F)
    return en.get(v, v) if isinstance(v, str) else v


def outline(F: dict) -> dict:
    """The draft as an agent needs to see it: tables with their columns, pages with their blocks, flows."""
    en = _strings(F)
    w = lambda v: en.get(v, v) if isinstance(v, str) else v  # noqa: E731
    out: dict = {"slug": (F["manifest"] or {}).get("slug", ""), "tables": {}, "pages": {}, "flows": {}}
    for n, b in F["data"].items():
        out["tables"][n] = [col_text(c) for c in b.get("columns") or [] if isinstance(c, dict)]
    for n, f in F["flows"].items():
        out["flows"][n] = {"title": w(f.get("title")), "trigger": (f.get("trigger") or {}).get("type")}
    for n, p in F["pages"].items():
        blocks = []
        for sec in ((p.get("layout") or {}).get("sections") or []):
            for col in sec.get("columns") or []:
                for b in col.get("widgets") or []:
                    blocks.append({"id": b.get("id"), "type": b.get("type"), "width": col.get("width", 12), **({"title": w(b.get("title"))} if b.get("title") else {}),
                                   **({"source": b.get("source")} if b.get("source") else {}), **({"text": w(b.get("text"))} if b.get("text") else {})})
        out["pages"][n] = {"title": w(p.get("title")), "blocks": blocks}
    return out


# ---- what the planner knows about this server -------------------------------------------------------------


def catalogue(db: Session) -> dict:
    from ..engine import loader, metrics as MX, providers

    tools = [{"slug": t.slug, "title": t.text(t.manifest.get("title", ""), "en")} for t in loader.registry().all()]
    provs = [{"name": p["name"], "kind": p["kind"], "title": p["title"], "columns": [c.get("key") for c in (p.get("columns") or [])][:14]} for p in providers.describe_all()]
    nums = [{"key": x.key, "label": x.label, "group": x.group} for x in MX.METRICS.values()]
    return {"tools_already_here": tools, "providers": provs, "numbers": nums, "roles": ["owner", "admin", "manager", "user"],
            "menu_sections": ["My tools", "Workspace", "Admin"], "icons": list(TEAM_ICONS), "column_types": ["text", "int", "number", "date", "bool", "choice(A|B|C)", "link(<table>)", "file"],
            "blocks": list(TEAM_BLOCKS), "flow_triggers": ["button", "row_added", "row_changed", "schedule"]}


# ---- the lifecycle ---------------------------------------------------------------------------------------


def local_stamp() -> str:
    from ..engine.flows import local_now

    return local_now().strftime("%d %b %H:%M").lstrip("0")


def create(db: Session, owner: User, brief: str, *, team_key: Optional[str] = None) -> Mission:
    """A job from the owner. She reads it first (status reading) — the question box appears only once she
    has questions. `team_key` sends it to a team; empty lets C-EXA choose."""
    from . import teams as T

    brief = (brief or "").strip()
    if len(brief) < 8:
        raise ValueError("Tell C-EXA a little more — what should the team do, and for whom?")
    if team_key and team_key not in T.TEAM_KEYS:
        raise ValueError("No such team.")
    T.ensure_teams(db)
    m = Mission(title=brief.split("\n")[0][:80], brief=brief[:6000], status="reading", created_by_id=owner.id, max_calls=max(20, int(settings.team_max_calls or 150)),
                team_key=team_key or "", kind="")
    db.add(m)
    db.flush()
    lead = _lead(db, m)
    say(db, m, brief, kind="owner", detail={"by": owner.name})
    say(db, m, "I've got it. Reading it through and working out what I need to ask you.", agent=lead)
    log(db, "cexa_job_created", actor=owner, target=f"job:{m.id}", detail=m.title)
    db.commit()
    jobs.submit(_job, m.id, "intake")
    return m


def _job(mid: int, step: str, arg: str = "") -> None:
    """A background step (intake, plan, revise, run) in its own session."""
    if step == "run":
        return run(mid)
    db = SessionLocal()
    try:
        m = db.get(Mission, mid)
        if m is None:
            return
        try:
            if step == "intake":
                _intake(db, m)
            elif step == "plan":
                _plan(db, m, feedback=arg)
            elif step == "revise":
                _revise(db, m, arg)
            db.commit()
        except (ModelError, OutOfCalls, BudgetExhausted, ValueError, TypeError, KeyError, AttributeError) as e:
            db.rollback()
            m = db.get(Mission, mid)
            m.status = {"intake": "failed", "plan": "plan" if m.plan else "failed", "revise": "review"}.get(step, "failed")
            why = "the model-call limit for this job" if isinstance(e, OutOfCalls) else (str(e)[:200] or type(e).__name__)
            m.state = f"I could not finish that step ({why}). Press Try again when the model is back."
            say(db, m, m.state, agent=_lead(db, m), kind="problem")
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def retry(db: Session, m: Mission) -> Mission:
    """A step that failed (the model was down, a reply that made no sense): do it again."""
    if m.status != "failed":
        raise ValueError("Nothing to try again on this job.")
    step = "plan" if (m.plan or m.kind in ("build", "change")) and (not m.questions or all(q.get("a") for q in m.questions)) and m.kind else "intake"
    m.status = "planning" if step == "plan" else "reading"
    m.state = "Trying again."
    say(db, m, "Try again.", kind="owner")
    db.commit()
    jobs.submit(_job, m.id, step)
    return m


def _intake(db: Session, m: Mission) -> None:
    from . import teams as T

    lead = _lead(db, m)
    _set(lead, "working", "reading the job")
    team_list = [{"team": t.key, "name": t.name, "does": t.purpose} for t in T.ensure_teams(db) if t.enabled]
    out = _ask(db, m, lead, None, "ceo_intake",
               "The owner gave you this job. You run a company of teams (INPUT.teams). Decide which team takes it: build (a new tool), change (improve a tool that is live — say which in 'tool'), "
               "advice (a list, a recommendation, a document: the answer, not a tool) or one of the standing teams if the job is exactly what that team checks. "
               + ("The owner already chose the team (INPUT.team). " if m.team_key else "")
               + "Say in one sentence what you understood, give it a short title, and list what you must know first (at most 4 questions, only those whose answers change the result; none if it is clear). "
               "Know what the server already has (INPUT.server) so you don't ask what you can see.",
               {"job": m.brief, "teams": team_list, "team": m.team_key or "", "server": catalogue(db)},
               {"team": "string", "tool": "string", "title": "string", "understood": "string", "questions": ["string"]})
    m.title = _text(out.get("title"), 80) or m.title
    key = m.team_key or str(out.get("team") or "").strip().lower()
    t = T.team(db, key) if key in T.TEAM_KEYS else None
    if t is None or not t.enabled:
        t = T.team(db, "build")
    if t.key == "change":
        tool = str(out.get("tool") or "").strip()
        from ..engine import loader

        live = loader.registry().tools
        if tool not in live:
            tool = next((s_ for s_ in live if s_ in m.brief.lower() or live[s_].text(live[s_].manifest.get("title", ""), "en").lower() in m.brief.lower()), "")
        if not tool:
            t = T.team(db, "build")
        else:
            m.plan = {**(m.plan or {}), "tool": tool}
    m.team_key, m.kind = t.key, t.kind
    say(db, m, _text(out.get("understood"), 600) or "Understood.", agent=lead)
    say(db, m, f"This goes to the {t.name} team.", agent=lead, kind="plan")
    if t.kind in ("report", "review"):
        start_report_here(db, m, t)
        return
    qs = [q for q in (out.get("questions") or []) if isinstance(q, str) and q.strip()][:4]
    if qs:
        m.questions = [{"q": q.strip()[:300], "a": ""} for q in qs]
        m.status = "asking"
        m.state = f"{len(qs)} question{'s' if len(qs) != 1 else ''} for you before I start."
        say(db, m, m.state, agent=lead, kind="plan", detail={"questions": [q["q"] for q in m.questions]})
        _notify(db, m, "C-EXA has questions", f"Before I start “{m.title}”, I need {len(qs)} answer{'s' if len(qs) != 1 else ''} from you.")
        _set(lead, "waiting", "waiting for your answers")
    elif m.kind == "advice":
        start_advice(db, m)
    else:
        m.status = "planning"
        db.flush()
        _plan(db, m)


def start_report_here(db: Session, m: Mission, t) -> None:
    """The owner asked for what a standing team does ("check security now"): this job becomes its run."""
    from . import teams as T

    agents = T.staff_job(db, m, t)
    m.kind = "report"
    m.status = "working"
    m.max_calls = max(m.calls + T.BUDGET["report"], 20)
    m.started_at = utcnow()
    _add_tasks(db, m, T.run_tasks(t, "report", agents), agents, round_no=m.round)
    db.commit()
    jobs.submit(_job, m.id, "run")


def start_advice(db: Session, m: Mission) -> None:
    """Advice needs no Go: the team only writes. It starts as soon as she has what she needs."""
    from . import teams as T

    t = T.team(db, "advice")
    agents = T.staff_job(db, m, t)
    m.status = "working"
    m.started_at = m.started_at or utcnow()
    m.state = f"The {t.name} team is writing it."
    _add_tasks(db, m, T.advice_tasks(agents, m.round), agents, round_no=m.round)
    db.commit()
    jobs.submit(_job, m.id, "run")


def answer(db: Session, m: Mission, answers: list, *, skip: bool = False) -> Mission:
    if m.status != "asking" or not m.questions:
        raise ValueError("C-EXA is not waiting for answers on this job.")
    qs = [dict(q) for q in (m.questions or [])]
    for i, q in enumerate(qs):
        a = answers[i] if i < len(answers) and isinstance(answers[i], str) else ""
        q["a"] = a.strip()[:1000] or ("(you said: use your judgement)" if skip else "")
    m.questions = qs
    say(db, m, "Answers: " + " · ".join(f"{q['q']} → {q['a'] or '—'}" for q in qs), kind="owner")
    if m.kind == "advice":
        start_advice(db, m)
        return m
    m.status = "planning"
    m.state = "Writing the plan."
    db.commit()
    jobs.submit(_job, m.id, "plan")
    return m


def _plan(db: Session, m: Mission, feedback: str = "") -> None:
    if m.kind == "change":
        return _plan_change(db, m, feedback)
    lead = _lead(db, m)
    _set(lead, "working", "writing the plan")
    payload = {"job": m.brief, "answers": [{"q": q["q"], "a": q.get("a", "")} for q in (m.questions or [])], "server": catalogue(db), "builder": BUILDER_GUIDE}
    if feedback:
        payload["previous_plan"] = {k: v for k, v in (m.plan or {}).items() if not k.startswith("_")}
        payload["owner_says_change"] = feedback
    out = _ask(db, m, lead, None, "ceo_plan",
               "Plan the job as a CBX-Connect tool the Builder can hold. Decide: the tool (title, icon, menu section, which roles use it), its data tables "
               "(name, purpose, columns as 'key:type' — type is text, int, number, date, bool, choice(A|B|C) for a pick-list such as a status or priority, "
               "link(<table>) for a row of another of its tables (never a name typed in), or file for a document on the row), "
               "its pages (name, title, purpose, which blocks in words, and roles only when a page is for some roles, e.g. an admin page), its flows (name, title, purpose, trigger), "
               "and — only if the job truly needs something the Builder cannot do (a new kind of block, a change to CBX-Connect itself) — core code changes "
               "(title, purpose, why). Your Build team does the work. "
               "Keep the tool small: at most 4 tables, 4 pages, 3 flows. Say in 'left_out' what you did not plan and why. "
               + ("The owner asked for changes to your previous plan (INPUT.owner_says_change): apply them." if feedback else ""),
               payload,
               {"title": "string", "summary": "string", "icon": "string", "section": "string", "roles": ["string"],
                "data": [{"name": "string", "purpose": "string", "columns": ["key:type"]}],
                "pages": [{"name": "string", "title": "string", "purpose": "string", "blocks": "string", "roles": ["string"]}],
                "flows": [{"name": "string", "title": "string", "purpose": "string", "trigger": "string"}],
                "code": [{"title": "string", "purpose": "string", "why": "string"}], "left_out": "string"})
    plan = normalise_plan(db, m, out)
    m.plan = plan
    m.title = plan["title"] or m.title
    m.tool_slug = plan["slug"]
    m.status = "plan"
    n_tasks = len(plan_tasks(plan))
    m.state = f"The plan is ready: {len(plan['data'])} table(s), {len(plan['pages'])} page(s), {len(plan['flows'])} flow(s)" + (f", {len(plan['code'])} core change(s)" if plan["code"] else "") + f"; {len(plan['team'])} agents, {n_tasks} tasks. Press Go when you're happy."
    say(db, m, plan["summary"] or m.state, agent=lead, kind="plan", detail={"plan": plan})
    _notify(db, m, "C-EXA's plan is ready", f"“{m.title}”: {m.state}")
    _set(lead, "waiting", "waiting for your Go")


def normalise_plan(db: Session, m: Mission, out: dict) -> dict:
    """What the lead wrote, made safe and whole: names that the loader accepts, a free folder name, the
    limits, and a team with every role the tasks need."""
    from ..engine import loader

    title = _text(out.get("title"), 60) or m.title or "New tool"
    base = _slug(title)
    if not loader.SLUG_RE.match(base):
        base = "tool-" + base
    live = set(loader.registry().tools) | set(loader.registry().broken)
    taken = lambda s: s in live or (draft_dir(s) / "tool.json").exists() and _job_of(s) != m.id  # noqa: E731
    slug = m.tool_slug or base
    i = 2
    while taken(slug):
        slug = f"{base[:36]}-{i}"
        i += 1
    roles = [r for r in (out.get("roles") or []) if r in ("owner", "admin", "manager", "user")] or ["owner", "admin", "manager", "user"]
    section = out.get("section") if out.get("section") in ("My tools", "Workspace", "Admin") else "My tools"
    if section == "Admin":
        roles = [r for r in roles if r in ("owner", "admin")] or ["owner", "admin"]
    icon = out.get("icon") if out.get("icon") in TEAM_ICONS else "board"
    data, seen = [], set()
    for d in (out.get("data") or [])[:4]:
        if not isinstance(d, dict):
            continue
        n = _name(d.get("name") or d.get("purpose") or "")
        if not n or n in seen:
            continue
        seen.add(n)
        cols = [c if isinstance(c, str) else col_text(c or {}) for c in (d.get("columns") or [])][:12]
        data.append({"name": n, "purpose": _text(d.get("purpose"), 300), "columns": cols})
    data = links_first(data)
    pages, seen = [], set()
    for p in (out.get("pages") or [])[:4]:
        if not isinstance(p, dict):
            continue
        n = _name(p.get("name") or p.get("title") or "")
        if not n or n in seen:
            continue
        seen.add(n)
        proles = [r for r in (p.get("roles") or []) if r in roles] if isinstance(p.get("roles"), list) else []
        pages.append({"name": n, "title": _text(p.get("title"), 60) or n.replace("_", " ").capitalize(), "purpose": _text(p.get("purpose"), 300), "blocks": _text(p.get("blocks"), 400),
                      **({"roles": proles} if proles and set(proles) != set(roles) else {})})
    if not pages:
        pages = [{"name": "main", "title": title, "purpose": "The tool's one page.", "blocks": "the main table and a form to add rows"}]
    flows, seen = [], set()
    for f in (out.get("flows") or [])[:3]:
        if not isinstance(f, dict):
            continue
        n = _name(f.get("name") or f.get("title") or "")
        if not n or n in seen:
            continue
        seen.add(n)
        raw = str(f.get("trigger") or "button").lower().strip()
        trig = raw if raw in ("button", "row_added", "row_changed", "schedule") else (
            "schedule" if any(w in raw for w in ("daily", "day", "morning", "week", "hour", "month", "schedule", "every")) else
            "row_added" if "add" in raw or "new" in raw else "row_changed" if "chang" in raw or "edit" in raw or "update" in raw else "button")
        flows.append({"name": n, "title": _text(f.get("title"), 60) or n.replace("_", " ").capitalize(), "purpose": _text(f.get("purpose"), 300), "trigger": trig})
    code = [{"title": _text(c.get("title"), 120), "purpose": _text(c.get("purpose"), 800), "why": _text(c.get("why"), 400)} for c in (out.get("code") or [])[:2] if isinstance(c, dict) and c.get("title")]
    team = team_for_plan(db, "build", code=bool(code), n_build=len(data) + len(pages) + len(flows))
    return {"title": title, "slug": slug, "summary": _text(out.get("summary"), 1200), "icon": icon, "section": section, "roles": roles,
            "data": data, "pages": pages, "flows": flows, "code": code, "team": team, "left_out": _text(out.get("left_out"), 800)}


def parse_col(s: str) -> dict:
    """A plan's column, 'key:type': 'status:choice(Open|In progress|Done)', 'project:link(projects)', 'evidence:file'."""
    key, _, rest = str(s).partition(":")
    m_ = re.match(r"^\s*([a-z_]+)\s*(?:\((.*)\)|:(.*))?\s*$", rest.strip().lower() if not rest.strip().lower().startswith("choice") else "choice" + rest.strip()[6:])
    ty = (m_.group(1) if m_ else rest.strip().lower()) or "text"
    arg = ((m_.group(2) if m_ else None) or (m_.group(3) if m_ else None) or "").strip()
    out: dict = {"key": _name(key) or "", "type": ty}
    if ty == "choice":
        opts = []
        for o in re.split(r"[|,/]", arg):
            o = o.strip()[:60]
            if o and o.lower() not in [x.lower() for x in opts]:
                opts.append(o)
        out["options"] = opts[:12]
    elif ty == "link":
        out["table"] = _name(arg)
    return out


def col_text(c: dict) -> str:
    ty = str(c.get("type") or "text").lower()
    if ty == "choice" and c.get("options"):
        return f"{c.get('key', '')}:choice({'|'.join(str(o) for o in c['options'])})"
    if ty == "link" and c.get("table"):
        return f"{c.get('key', '')}:link({c['table']})"
    return f"{c.get('key', '')}:{ty}"


def links_first(data: list[dict]) -> list[dict]:
    """Tables in an order where a table comes after the tables its links point at (a link needs its table)."""
    names = {d["name"] for d in data}
    deps = {d["name"]: {parse_col(c).get("table") for c in d["columns"] if parse_col(c).get("type") == "link"} & names - {d["name"]} for d in data}
    out, done = [], set()
    for _ in range(len(data) + 1):
        for d in data:
            if d["name"] not in done and deps[d["name"]] <= done:
                out.append(d)
                done.add(d["name"])
    return out + [d for d in data if d["name"] not in done]


def team_for_plan(db: Session, key: str, *, code: bool, n_build: int = 99) -> list[dict]:
    """The standing team's members who will work this job: its lead, its developers (one when the job is
    small), the core developer only when there is core code, and the rest. The owner shapes the team on
    the Teams tab; the plan only picks from it."""
    from . import teams as T

    t = T.team(db, key)
    if t is None:
        return normalise_team([], n_build=n_build, code=code)
    out, devs = [], 0
    for mm in T.members(db, t):
        role = "teamlead" if mm.lead else mm.role
        if role == "core" and not code:
            continue
        if role == "developer":
            devs += 1
            if devs > (2 if n_build > 4 else 1):
                continue
        out.append({"name": mm.name, "role": role, "focus": mm.focus})
    return out or normalise_team([], n_build=n_build, code=code)


def role_label(role: str) -> str:
    from . import teams as T

    if role == "teamlead":
        return "Team lead"
    return (ROLE_INFO.get(role) or T.ROLES.get(role) or (role.capitalize(), ""))[0]


def normalise_team(team: list, *, n_build: int, code: bool) -> list[dict]:
    out, names = [], set()
    alias = {"dev": "developer", "developer": "developer", "engineer": "developer", "builder": "developer", "visual": "designer", "designer": "designer", "design": "designer",
             "tester": "tester", "qa": "tester", "test": "tester", "helper": "helper", "assistant": "helper", "writer": "helper", "translator": "helper", "core": "core", "core developer": "core"}
    for a in team if isinstance(team, list) else []:
        if not isinstance(a, dict):
            continue
        role = alias.get(str(a.get("role", "")).strip().lower())
        if role is None or role == "lead":
            continue
        if role == "core" and not code:
            continue
        n = sum(1 for x in out if x["role"] == role)
        if (role == "developer" and n >= 2) or (role != "developer" and n >= 1):
            continue
        name = re.sub(r"[^A-Za-z0-9 -]", "", str(a.get("name") or "")).strip()[:24]
        if not name or name.lower() in names:
            name = _default_name(role, n + 1)
        names.add(name.lower())
        out.append({"name": name, "role": role, "focus": _text(a.get("focus"), 160)})
    want_devs = 2 if n_build > 4 else 1
    while sum(1 for x in out if x["role"] == "developer") < want_devs:
        k = sum(1 for x in out if x["role"] == "developer") + 1
        out.append({"name": _unique(_default_name("developer", k), names), "role": "developer", "focus": "data and flows" if k == 1 else "pages"})
    for role in ("designer", "tester", "helper") + (("core",) if code else ()):
        if not any(x["role"] == role for x in out):
            out.append({"name": _unique(_default_name(role, 1), names), "role": role, "focus": {"designer": "the look", "tester": "the checks", "helper": "rows, words, README", "core": "core code"}[role]})
    return sorted(out, key=lambda x: (ROLE_ORDER.index(x["role"]), x["name"]))


def _default_name(role: str, n: int) -> str:
    return {"developer": f"Dev-{n}", "designer": "Iris", "tester": "Tess", "helper": "Hal", "core": "Core-1"}.get(role, f"Agent-{n}")


def _unique(name: str, names: set) -> str:
    base, i = name, 2
    while name.lower() in names:
        name = f"{base}-{i}"
        i += 1
    names.add(name.lower())
    return name


def _job_of(slug: str) -> Optional[int]:
    try:
        return int(json.loads((draft_dir(slug) / ".cexa-job").read_text(encoding="utf-8")).get("job"))
    except (OSError, ValueError, TypeError):
        return None


def plan_tasks(plan: dict) -> list[dict]:
    """The plan as tasks, in the order they can happen. Who does what follows the roles: data and flows go
    to the first developer, pages to the second (or the first), looks to the designer, the checks to the
    tester, the small jobs to the helper, core code to the core developer."""
    team = plan.get("team") or []
    devs = [a["name"] for a in team if a["role"] == "developer"] or ["Dev-1"]
    who = {r: next((a["name"] for a in team if a["role"] == r), None) for r in ("designer", "tester", "helper", "core")}
    d1, d2 = devs[0], devs[-1]
    data_keys = [f"data:{d['name']}" for d in plan["data"]]
    flow_keys = [f"flow:{f['name']}" for f in plan["flows"]]
    page_keys = [f"page:{p['name']}" for p in plan["pages"]]
    t: list[dict] = []
    for d in plan["data"]:
        t.append({"key": f"data:{d['name']}", "kind": "data", "target": d["name"], "agent": d1, "title": f"Build the {d['name'].replace('_', ' ')} table", "detail": d["purpose"], "depends": []})
    for f in plan["flows"]:
        t.append({"key": f"flow:{f['name']}", "kind": "flow", "target": f["name"], "agent": d1, "title": f"Build the flow “{f['title']}”", "detail": f["purpose"], "depends": list(data_keys)})
    for p in plan["pages"]:
        t.append({"key": f"page:{p['name']}", "kind": "page", "target": p["name"], "agent": d2, "title": f"Build the page “{p['title']}”", "detail": (p["purpose"] + " Blocks: " + p["blocks"]).strip(), "depends": data_keys + flow_keys})
    t.append({"key": "sample", "kind": "sample", "target": "", "agent": who["helper"], "title": "Make up sample rows for the screenshots", "detail": "Believable rows — never real data.", "depends": list(data_keys)})
    for p in plan["pages"]:
        t.append({"key": f"look:{p['name']}", "kind": "look", "target": p["name"], "agent": who["designer"], "title": f"Look at “{p['title']}” against CBX-Connect's style", "detail": "", "depends": [f"page:{p['name']}", "sample"]})
    t.append({"key": "words", "kind": "words", "target": "", "agent": who["helper"], "title": "Translate every word into Arabic, Portuguese and French", "detail": "", "depends": page_keys + flow_keys + data_keys + [f"look:{p['name']}" for p in plan["pages"]]})
    t.append({"key": "readme", "kind": "readme", "target": "", "agent": who["helper"], "title": "Write the tool's README", "detail": "What it is, who uses it, where each thing lives, how to change it.", "depends": page_keys + flow_keys})
    for i, c in enumerate(plan.get("code") or []):
        t.append({"key": f"code:{i + 1}", "kind": "code", "target": str(i + 1), "agent": who["core"], "title": c["title"], "detail": (c["purpose"] + ("\nWhy: " + c["why"] if c.get("why") else "")).strip(), "depends": []})
    t.append({"key": "test", "kind": "test", "target": "", "agent": who["tester"], "title": "Test the whole draft", "detail": "Loads clean, every page draws, every flow runs dry, every word is there.", "depends": [x["key"] for x in t if x["kind"] not in ("code",)]})
    t.append({"key": "summary", "kind": "summary", "target": "", "agent": next((a["name"] for a in team if a["role"] == "teamlead"), None), "title": "Check the work and write it up for the owner", "detail": "", "depends": [x["key"] for x in t]})
    return t


def go(db: Session, m: Mission, by: User) -> Mission:
    if m.status != "plan":
        raise ValueError("There is no plan waiting for Go on this job.")
    plan = m.plan or {}
    lead = _lead(db, m)
    agents = {lead.name: lead}
    from . import teams as T

    tm = T.team(db, m.team_key or "build")
    models = {mm.name: T.model_for(mm) for mm in (T.members(db, tm) if tm else [])}
    for a in plan.get("team") or []:
        ag = MissionAgent(mission_id=m.id, name=a["name"], role=a["role"], focus=a.get("focus", ""), model=models.get(a["name"]) or model_for_role(a["role"]), status="waiting")
        db.add(ag)
        db.flush()
        agents[a["name"]] = ag
    owner = _owner(db, m)
    if m.kind == "change":
        _copy_live_to_draft(db, m)
        F = _read(m)
        _add_tasks(db, m, change_tasks(m, F, plan.get("changes") or [], agents, round_no=1), agents, round_no=1)
    else:
        _add_tasks(db, m, plan_tasks(plan), agents, round_no=1)
        # the draft starts with the Tool block: name, icon, menu place, who, owner
        F = _read(m)
        F["manifest"] = {"slug": m.tool_slug, "title": plan.get("title") or m.title, "icon": plan.get("icon", "board"), "menu": {"section": plan.get("section", "My tools"), "order": 60},
                         "access": {"roles": plan.get("roles") or ["owner", "admin"]}, "home": plan["pages"][0]["name"] if plan.get("pages") else "", "owner": owner.email if owner else ""}
        _write(db, m, F, agent=lead, what="started the draft (the Tool block)")
    m.status = "working"
    m.started_at = m.started_at or utcnow()
    m.state = "The team is working."
    lines = []
    for ag in agents.values():
        if ag.role == "lead":
            continue
        n = db.query(MissionTask).filter(MissionTask.mission_id == m.id, MissionTask.agent_id == ag.id).count()
        lines.append(f"{ag.name} ({role_label(ag.role).lower()}): {n} task{'s' if n != 1 else ''}")
    say(db, m, "Go. " + "; ".join(lines) + f". The draft is {m.tool_slug} in the Builder — nothing is live until you release it.", agent=lead, kind="plan")
    log(db, "cexa_job_go", actor=by, target=f"job:{m.id}", detail=f"{m.title}; {len(agents) - 1} agents")
    _set(lead, "working", "watching the team")
    db.commit()
    jobs.submit(_job, m.id, "run")
    return m


def _add_tasks(db: Session, m: Mission, tasks: list[dict], agents: dict, *, round_no: int, parent: Optional[MissionTask] = None) -> list[MissionTask]:
    n0 = db.query(MissionTask).filter(MissionTask.mission_id == m.id).count()
    out = []
    lead = _lead(db, m)
    for i, t in enumerate(tasks):
        ag = agents.get(t.get("agent") or "") or lead
        row = MissionTask(mission_id=m.id, agent_id=ag.id, parent_id=parent.id if parent else t.get("parent_id"), key=t["key"], kind=t["kind"], target=t.get("target", ""), title=t["title"][:300],
                          detail=t.get("detail", ""), depends=t.get("depends") or [], status="todo", round=round_no, order=n0 + i,
                          steps=[{"text": s, "done": False} for s in STEPS.get(t["kind"], [])])
        db.add(row)
        out.append(row)
    db.flush()
    return out


STEPS = {
    "data": ["Read the plan", "Write the table", "Check it", "Save it to the draft"],
    "flow": ["Read the plan and the tables", "Write the steps", "Check it", "Run it dry", "Save it (switched off)"],
    "page": ["Read the plan, tables and flows", "Lay out the blocks", "Check it", "Save it to the draft"],
    "fix": ["Read what is wrong", "Change it", "Check it", "Save it"],
    "sample": ["Make up rows for every table"],
    "look": ["Take a screenshot", "Compare it with CBX-Connect's look", "Score it", "Ask for fixes if it needs them"],
    "words": ["Arabic", "Portuguese", "French"],
    "readme": ["Write it", "Save it"],
    "test": ["The folder loads", "Every page draws", "Every flow runs dry", "Every word is there"],
    "code": ["Copy the app to a sandbox", "Write the change", "Run the tests", "Fix and run again if red"],
    "summary": ["Read every result", "Write it up"],
}


def _step(task: MissionTask, i: int) -> None:
    steps = list(task.steps or [])
    for j in range(min(i + 1, len(steps))):
        steps[j] = {**steps[j], "done": True}
    task.steps = steps


# ---- owner controls --------------------------------------------------------------------------------------


def replan(db: Session, m: Mission, text: str) -> Mission:
    if m.status != "plan":
        raise ValueError("The plan can be changed while it waits for Go.")
    text = (text or "").strip()
    if not text:
        raise ValueError("Say what to change.")
    say(db, m, text, kind="owner", detail={"change_plan": True})
    m.status = "planning"
    m.state = "Changing the plan."
    db.commit()
    jobs.submit(_job, m.id, "plan", text)
    return m


def pause(db: Session, m: Mission, on: bool) -> Mission:
    if m.status != "working":
        raise ValueError("Only a job that is working can be paused.")
    m.paused = on
    if not on and m.calls >= m.max_calls:
        m.max_calls = m.calls + 50
        say(db, m, "Another 50 model calls for this job.", kind="owner")
    say(db, m, "Paused — the team finishes the step it is on and waits." if on else "Carry on.", kind="owner")
    m.state = "Paused." if on else "The team is working."
    db.commit()
    if not on:
        jobs.submit(_job, m.id, "run")
    return m


def stop(db: Session, m: Mission, by: User) -> Mission:
    if m.status in ("done", "stopped"):
        return m
    m.status = "stopped"
    m.finished_at = utcnow()
    m.state = "Stopped. The draft stays in the Builder as it is; discard it there if you don't want it."
    for t in db.query(MissionTask).filter(MissionTask.mission_id == m.id, MissionTask.status.in_(["todo", "working"])).all():
        t.status = "skipped"
    for a in db.query(MissionAgent).filter(MissionAgent.mission_id == m.id).all():
        _set(a, "done", "")
    say(db, m, "Stopped by you.", kind="owner")
    log(db, "cexa_job_stopped", actor=by, target=f"job:{m.id}", detail=m.title)
    db.commit()
    return m


def note(db: Session, m: Mission, text: str) -> Mission:
    """The owner tells the team something while it works: every agent's next prompt carries it."""
    text = (text or "").strip()[:1000]
    if not text:
        raise ValueError("Say something for the team.")
    m.notes = list(m.notes or []) + [{"at": utcnow().isoformat(), "text": text}]
    say(db, m, text, kind="owner", detail={"note": True})
    say(db, m, "Noted — the team has it from the next step on.", agent=_lead(db, m))
    db.commit()
    return m


def revise(db: Session, m: Mission, text: str) -> Mission:
    if m.status != "review":
        raise ValueError("Changes can be asked for once the work is in for review.")
    text = (text or "").strip()
    if not text:
        raise ValueError("Say what to change.")
    say(db, m, text, kind="owner", detail={"changes": True})
    m.status = "planning"
    m.state = "Working out the changes."
    db.commit()
    jobs.submit(_job, m.id, "revise", text)
    return m


def close(db: Session, m: Mission, by: User) -> Mission:
    if m.status != "review":
        raise ValueError("Only a job in review can be closed.")
    m.status = "done"
    m.finished_at = utcnow()
    m.state = "Closed. The draft is in the Builder for you to release."
    for a in db.query(MissionAgent).filter(MissionAgent.mission_id == m.id).all():
        _set(a, "done", "")
    say(db, m, "Closed by you.", kind="owner")
    log(db, "cexa_job_closed", actor=by, target=f"job:{m.id}", detail=m.title)
    db.commit()
    return m


def _revise(db: Session, m: Mission, text: str) -> None:
    lead = _lead(db, m)
    if m.kind == "advice":
        m.round += 1
        m.notes = list(m.notes or []) + [{"at": utcnow().isoformat(), "text": text}]
        start_advice(db, m)
        return
    if m.kind in ("report", "review"):
        raise ValueError("A team's report is not changed — give its lead a job instead.")
    F = _read(m)
    out = _ask(db, m, lead, None, "ceo_revise",
               "The owner looked at the team's work and asked for changes (INPUT.owner_says). Turn them into tasks: each is a page, table or flow to "
               "change (kind page|data|flow, the name — an existing one or a new one — and a one-line instruction). Only what was asked.",
               {"owner_says": text, "draft": outline(F)}, {"changes": [{"kind": "string", "name": "string", "instruction": "string"}]})
    m.round += 1
    agents = {a.name: a for a in db.query(MissionAgent).filter(MissionAgent.mission_id == m.id).all()}
    tasks = change_tasks(m, F, out.get("changes") or [], agents, round_no=m.round)
    if not tasks:
        m.status = "review"
        m.state = "I couldn't turn that into changes — say it another way?"
        say(db, m, m.state, agent=lead)
        return
    _add_tasks(db, m, tasks, agents, round_no=m.round)
    n = sum(1 for t in tasks if t["kind"] in ("fix", "page", "data", "flow"))
    m.status = "working"
    m.state = f"Round {m.round}: {n} change{'s' if n != 1 else ''}."
    say(db, m, m.state + " " + "; ".join(t["title"] for t in tasks[:n]), agent=lead, kind="plan")
    db.flush()
    db.commit()
    jobs.submit(_job, m.id, "run")


def change_tasks(m: Mission, F: dict, changes: list, agents: dict, *, round_no: int) -> list[dict]:
    """Changes (kind page|data|flow, a name, an instruction) as tasks — then the sample rows, a look at every
    changed page, the words, the test and the write-up. Used by Change jobs and by "ask for changes"."""
    team = [{"name": a.name, "role": a.role} for a in agents.values()]
    lead = next((a["name"] for a in team if a["role"] == "teamlead"), None) or next((a["name"] for a in team if a["role"] == "lead"), None)
    devs = [a["name"] for a in team if a["role"] == "developer"] or [lead]
    who = {r: next((a["name"] for a in team if a["role"] == r), lead) for r in ("designer", "tester", "helper")}
    r = "" if round_no == 1 and m.kind == "change" else f":r{round_no}"
    tasks, pages = [], []
    for c in (changes or [])[:8]:
        if not isinstance(c, dict):
            continue
        kind = c.get("kind") if c.get("kind") in ("page", "data", "flow") else "page"
        name = _name(c.get("name") or "")
        if not name:
            continue
        instr = _text(c.get("instruction"), 600)
        exists = name in F["pages" if kind == "page" else kind if kind == "data" else "flows"]
        k = f"{kind}:{name}{r or ':c'}"
        tasks.append({"key": k, "kind": "fix" if exists else kind, "target": f"{kind}:{name}" if exists else name, "agent": devs[0] if kind != "page" else devs[-1],
                      "title": f"{'Change' if exists else 'Add'} the {kind} “{name.replace('_', ' ')}”: {instr}"[:300], "detail": instr, "depends": [x["key"] for x in tasks if x["kind"] in ("data", "fix") and x["target"].startswith("data")]})
        if kind == "page":
            pages.append(name)
    if not tasks:
        return []
    keys = [t["key"] for t in tasks]
    rr = r or ":c"
    if pages:
        tasks.append({"key": f"sample{rr}", "kind": "sample", "target": "", "agent": who["helper"], "title": "Refresh the sample rows", "detail": "", "depends": keys})
        for p in pages:
            tasks.append({"key": f"look:{p}{rr}", "kind": "look", "target": p, "agent": who["designer"], "title": f"Look at “{p.replace('_', ' ')}” again", "detail": "", "depends": keys + [f"sample{rr}"]})
    tasks.append({"key": f"words{rr}", "kind": "words", "target": "", "agent": who["helper"], "title": "Translate the new words", "detail": "", "depends": [t["key"] for t in tasks]})
    tasks.append({"key": f"test{rr}", "kind": "test", "target": "", "agent": who["tester"], "title": "Test the whole draft", "detail": "", "depends": [t["key"] for t in tasks]})
    tasks.append({"key": f"summary{rr}", "kind": "summary", "target": "", "agent": lead, "title": "Check the changes and write them up", "detail": "", "depends": [t["key"] for t in tasks]})
    return tasks


def _copy_live_to_draft(db: Session, m: Mission) -> None:
    """A Change job works on a copy: the live folder laid down as a draft (assets too). The live tool is
    never touched; the owner releases the draft like any other."""
    import shutil

    from ..api.builder_api import write_folder
    from ..engine import loader

    live = loader.registry().tools.get(m.tool_slug)
    if live is None:
        raise ValueError(f"'{m.tool_slug}' is not a live tool any more.")
    d = draft_dir(m.tool_slug)
    if (d / "tool.json").exists() and _job_of(m.tool_slug) != m.id:
        raise ValueError(f"There is already a draft of {m.tool_slug} in the Builder. Release or discard it first.")
    with _DRAFT_LOCK:
        d.mkdir(parents=True, exist_ok=True)
        write_folder(d, dict(live.manifest), dict(live.pages), dict(live.data), {k: dict(v) for k, v in live.languages.items()}, live.readme, dict(live.flows))
        if (live.path / "assets").is_dir():
            shutil.copytree(live.path / "assets", d / "assets", dirs_exist_ok=True)
        (d / ".cexa-job").write_text(json.dumps({"job": m.id, "title": m.title, "round": m.round, "change_of": m.tool_slug}), encoding="utf-8")
    log(db, "cexa_draft_written", actor=_cexa(db), actor_label="axl→team:change", target=m.tool_slug, detail=f"job {m.id}: copied the live tool into a draft")


def _plan_change(db: Session, m: Mission, feedback: str = "") -> None:
    from ..engine import loader
    from . import teams as T

    lead = _lead(db, m)
    slug = (m.plan or {}).get("tool") or m.tool_slug
    live = loader.registry().tools.get(slug) or loader.registry().broken.get(slug)  # a broken tool is changed to fix it
    if live is None:
        raise ValueError(f"'{slug}' is not a live tool — give the job to Build instead.")
    if (draft_dir(slug) / "tool.json").exists() and _job_of(slug) != m.id:
        m.status = "failed"
        m.state = f"There is already a draft of {slug} in the Builder. Release or discard it first, then press Try again."
        say(db, m, m.state, agent=lead, kind="problem")
        return
    _set(lead, "working", "working out the change")
    F = {"manifest": live.manifest, "pages": live.pages, "data": live.data, "flows": live.flows, "languages": live.languages}
    payload = {"job": m.brief, "answers": [{"q": q["q"], "a": q.get("a", "")} for q in (m.questions or [])], "tool": outline(F), "server": catalogue(db), "builder": BUILDER_GUIDE,
               **({"problems_to_fix": list(live.errors)[:20]} if live.errors else {}), **({"builder_warnings": [w for w in live.warnings if "read-only" in w][:10]} if live.warnings else {})}
    if feedback:
        payload["previous_plan"] = {k: v for k, v in (m.plan or {}).items() if not k.startswith("_")}
        payload["owner_says_change"] = feedback
    out = _ask(db, m, lead, None, "ceo_change",
               "Plan the change to this live tool (INPUT.tool). List the changes as tasks: each is a page, table or flow to change or add (kind page|data|flow, "
               "its name — an existing one or a new one — and a one-line instruction). Only what the job asks — and every problem in INPUT.problems_to_fix, and any rule "
               "in INPUT.builder_warnings that the Builder cannot open (rewrite it in the rule shape). Say in 'left_out' what you did not plan and why. "
               + ("Apply the owner's changes to your previous plan (INPUT.owner_says_change)." if feedback else ""),
               payload, {"title": "string", "summary": "string", "changes": [{"kind": "string", "name": "string", "instruction": "string"}], "left_out": "string"})
    changes = [c for c in (out.get("changes") or []) if isinstance(c, dict) and c.get("name")][:8]
    title = live.text(live.manifest.get("title", ""), "en")
    m.plan = {"tool": slug, "title": _text(out.get("title"), 80) or f"Change {title}", "slug": slug, "summary": _text(out.get("summary"), 1200), "changes": changes,
              "team": team_for_plan(db, "change", code=False), "left_out": _text(out.get("left_out"), 800), "change_of": title}
    m.title = m.plan["title"]
    m.tool_slug = slug
    m.status = "plan"
    m.state = f"The plan is ready: {len(changes)} change{'s' if len(changes) != 1 else ''} to {title}, in a copy draft. Press Go when you're happy."
    say(db, m, m.plan["summary"] or m.state, agent=lead, kind="plan", detail={"plan": m.plan})
    _notify(db, m, "C-EXA's plan is ready", f"“{m.title}”: {m.state}")
    _set(lead, "waiting", "waiting for your Go")



# ---- the runner ------------------------------------------------------------------------------------------

_RUNNING: set[int] = set()
_RUN_LOCK = threading.Lock()


def running() -> set[int]:
    return set(_RUNNING)


def run(mid: int) -> None:
    with _RUN_LOCK:
        if mid in _RUNNING:
            return
        _RUNNING.add(mid)
    try:
        idle = 0
        while True:
            db = SessionLocal()
            try:
                m = db.get(Mission, mid)
                if m is None or m.status != "working" or m.paused:
                    return
                _poll_code(db, m)
                t = next_task(db, m)
                if t is None:
                    if settled(db, m):
                        _finish(db, m)
                        db.commit()
                        return
                    db.commit()
                    if jobs._inline:  # tests: nothing will change by waiting
                        return
                    idle += 1
                    time.sleep(10 if idle < 360 else 60)
                    continue
                idle = 0
                work(db, m, t)
                db.commit()
            except OutOfCalls:
                db.rollback()
                m = db.get(Mission, mid)
                m.paused = True
                m.state = f"I've used {m.calls} model calls — the most one job may spend. Press Carry on to give the team another 50."
                say(db, m, m.state, agent=_lead(db, m), kind="problem")
                _notify(db, m, "C-EXA paused a job", f"“{m.title}” reached its model-call limit. Carry on gives it 50 more.")
                db.commit()
                return
            except Stopped:
                db.rollback()
                return
            finally:
                db.close()
    finally:
        with _RUN_LOCK:
            _RUNNING.discard(mid)


def next_task(db: Session, m: Mission) -> Optional[MissionTask]:
    tasks = db.query(MissionTask).filter(MissionTask.mission_id == m.id).order_by(MissionTask.order).all()
    state = {t.key: t.status for t in tasks}
    for t in tasks:
        if t.status != "todo":
            continue
        if all(state.get(k, "done") in ("done", "problem", "skipped") for k in (t.depends or [])):
            return t
    return None


def settled(db: Session, m: Mission) -> bool:
    return not db.query(MissionTask).filter(MissionTask.mission_id == m.id, MissionTask.status.in_(["todo", "working", "testing"])).count()


def recover(db: Session) -> list[int]:
    """After a restart: tasks left half-done go back to To do, and every working job gets a runner again."""
    out = []
    for m in db.query(Mission).filter(Mission.status == "working").all():
        if m.id in _RUNNING:
            continue
        for t in db.query(MissionTask).filter(MissionTask.mission_id == m.id, MissionTask.status == "working").all():
            t.status = "todo"
        out.append(m.id)
    db.commit()
    for mid in out:
        jobs.submit(_job, mid, "run")
    return out


@jobs.routine("cexa-team", 60)
def team_routine() -> None:
    db = SessionLocal()
    try:
        recover(db)
    finally:
        db.close()


def work(db: Session, m: Mission, t: MissionTask) -> None:
    ag = db.get(MissionAgent, t.agent_id) if t.agent_id else _lead(db, m)
    t.status = "working"
    t.started_at = utcnow()
    t.attempts += 1
    _set(ag, "working", t.title, t)
    m.state = f"{ag.name}: {t.title}"
    db.commit()
    fn = WORKERS.get(t.kind)
    try:
        ok, sentence = fn(db, m, ag, t) if fn else (False, f"no worker for {t.kind}")
    except (OutOfCalls, Stopped):
        t.status = "todo"
        t.attempts -= 1
        _set(ag, "waiting", "")
        raise
    except Exception as e:  # noqa: BLE001 — whatever went wrong is the task's problem, never the runner's
        db.rollback()
        t = db.get(MissionTask, t.id)
        ag = db.get(MissionAgent, ag.id)
        ok, sentence = False, f"{type(e).__name__}: {str(e)[:300]}"
    if t.status == "testing":  # handed to a sandbox build that finishes on its own
        _set(ag, "waiting", f"waiting for the sandbox tests: {t.title}")
        return
    if ok:
        t.status = "done"
        _step(t, len(t.steps or []))
        say(db, m, sentence, agent=ag, task=t, kind="done")
    else:
        t.status = "problem"
        t.output = {**(t.output or {}), "problem": sentence}
        say(db, m, sentence, agent=ag, task=t, kind="problem")
        say(db, m, f"Leaving “{t.title}” for you to look at; the rest of the team carries on.", agent=_lead(db, m))
    t.finished_at = utcnow()
    _set(ag, "waiting" if db.query(MissionTask).filter(MissionTask.agent_id == ag.id, MissionTask.status == "todo").count() else "done", "")


def _notes(m: Mission) -> list[str]:
    return [n.get("text", "") for n in (m.notes or [])][-6:]


def _plan_item(m: Mission, kind: str, name: str) -> dict:
    return next((x for x in (m.plan or {}).get(kind, []) if x.get("name") == name), {"name": name, "purpose": "", "title": name})


def _retrying(db: Session, m: Mission, ag: MissionAgent, t: MissionTask, name: str, instruction: str, payload: dict, schema: dict, apply) -> tuple[bool, str]:
    """Ask, apply, check — and if the check finds problems, ask again with them, up to MAX_ATTEMPTS."""
    problems: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        p = dict(payload)
        if problems:
            p["problems_last_time"] = problems[:12]
            say(db, m, f"The check found {len(problems)} problem{'s' if len(problems) != 1 else ''} — fixing (try {attempt}).", agent=ag, task=t, kind="fix", detail={"problems": problems[:12]})
        if _notes(m):
            p["owner_notes"] = _notes(m)
        try:
            out = _ask(db, m, ag, t, name, instruction, p, schema)
        except ModelError as e:
            problems = [f"the last reply could not be read as the JSON asked for ({str(e)[:120]}) — reply with only the JSON object"]
            db.commit()
            continue
        _step(t, 1)
        ok, problems, sentence = apply(out)
        _step(t, 2)
        db.commit()
        if ok:
            return True, sentence
    return False, f"Still {len(problems)} problem{'s' if len(problems) != 1 else ''} after {MAX_ATTEMPTS} tries: " + "; ".join(problems[:4])


# ---- the workers ------------------------------------------------------------------------------------------


def w_data(db: Session, m: Mission, ag: MissionAgent, t: MissionTask) -> tuple[bool, str]:
    from ..engine.loader import COLUMN_TYPES

    item = _plan_item(m, "data", t.target)

    planned = {pc["key"]: pc for pc in (parse_col(x) for x in (item or {}).get("columns") or []) if pc.get("key")}
    plan_tables = {d.get("name") for d in (m.plan or {}).get("data") or []}

    def apply(out: dict):
        from ..engine.loader import FILE_TYPES

        cols, seen = [], set()
        for c in out.get("columns") or []:
            if isinstance(c, str):
                c = parse_col(c)
            if not isinstance(c, dict):
                continue
            key = _name(c.get("key"))
            if not key or key in ("id", "created_at", "created_by") or key in seen:
                continue
            seen.add(key)
            ty = str(c.get("type") or "text").lower()
            ty = ty if ty in COLUMN_TYPES and ty != "json" else {"datetime": "date", "integer": "int", "float": "number", "decimal": "number", "money": "number", "boolean": "bool", "string": "text",
                                                                 "select": "choice", "enum": "choice", "dropdown": "choice", "reference": "link", "lookup": "link", "attachment": "file", "upload": "file", "document": "file"}.get(ty, "text")
            pc = planned.get(key) or {}
            col = {"key": key, "type": ty, "label": _text(c.get("label"), 60) or key.replace("_", " ").capitalize(), **({"required": True} if c.get("required") else {})}
            if ty == "choice":
                opts = []
                for o in (c.get("options") if isinstance(c.get("options"), list) else None) or pc.get("options") or []:
                    o = _text(o, 60)
                    if o and o.lower() not in [x.lower() for x in opts] and loader_opt(o) not in [loader_opt(x) for x in opts]:
                        opts.append(o)
                if len(opts) >= 2:
                    col["options"] = opts[:20]
                else:
                    col["type"] = "text"
            elif ty == "link":
                tgt = _name(c.get("table") or pc.get("table") or "")
                if tgt and (tgt in plan_tables or tgt in _read(m)["data"]):
                    col["table"] = tgt
                    if c.get("show"):
                        col["show"] = _name(c["show"])
                else:
                    col["type"] = "text"
            elif ty == "file":
                acc = [str(a).lower().lstrip(".") for a in (c.get("accept") or []) if str(a).lower().lstrip(".") in FILE_TYPES]
                if acc:
                    col["accept"] = acc
                try:
                    col["max_mb"] = max(1, min(int(c.get("max_mb") or 10), 25))
                except (TypeError, ValueError):
                    col["max_mb"] = 10
            elif ty == "person":
                if c.get("who") == "team":
                    col["who"] = "team"
            elif ty == "calc":
                if isinstance(c.get("calc"), dict):
                    col["calc"] = c["calc"]
                else:
                    col["type"] = "text"
            dv = c.get("default")
            if (ty == "choice" and dv in (col.get("options") or [])) or (ty == "date" and dv == "today") or (ty == "person" and dv == "me"):
                col["default"] = dv
            cols.append(col)
        F = _read(m)
        keys = {c["key"] for c in cols}
        scope = out.get("scope") if out.get("scope") in ("all", "mine", "team") else "all"
        tbl = {"kind": "table", "columns": cols[:15], "write": out.get("write") if out.get("write") in ("everyone", "managers", "admins", "nobody") else "everyone", "scope": scope}
        uq = [[_name(k) for k in g] for g in (out.get("unique") or []) if isinstance(g, list)]
        uq = [g for g in uq if g and all(k in keys for k in g)]
        if uq:
            tbl["unique"] = uq
        sh = out.get("share") if isinstance(out.get("share"), dict) else None
        if scope == "mine" and sh and any(c["key"] == sh.get("field") and sh.get("value") in (c.get("options") or []) for c in cols):
            tbl["share"] = {"field": sh["field"], "value": sh["value"]}
        F["data"][t.target] = tbl
        # a show column the target does not have: let the engine pick (its first text column)
        for col in cols:
            if col.get("type") == "link" and col.get("show") and col["table"] in F["data"] and col["show"] not in {x.get("key") for x in F["data"][col["table"]].get("columns") or []}:
                col.pop("show")
        td = _write(db, m, F, agent=ag, what=f"table {t.target}")
        # a link to a table of the plan that is not written yet is fine: it lands in its own task
        probs = [p_ for p_ in problems_for(td.errors, f"data/{t.target}") if not any(f"a table this tool does not have ('{x}')" in p_ for x in plan_tables)]
        t.output = {"files": [f"data/{t.target}.json"], "columns": [c["key"] for c in cols]}
        return (not probs and bool(cols)), probs or ([] if cols else ["the table has no columns"]), f"Wrote the {t.target.replace('_', ' ')} table: {len(cols)} columns ({', '.join(c['key'] for c in cols[:8])})."

    return _retrying(db, m, ag, t, "team_data", "Write this data table for the tool. Follow INPUT.spec exactly.",
                     {"table": item, "tool": (m.plan or {}).get("title"), "job": m.brief[:1500], "spec": SPEC_DATA, "instruction": t.detail,
                      **({"current": _read(m)["data"].get(t.target)} if _read(m)["data"].get(t.target) else {})}, {"columns": [{"key": "string", "type": "string", "label": "string"}], "write": "string", "scope": "string"}, apply)


def w_flow(db: Session, m: Mission, ag: MissionAgent, t: MissionTask) -> tuple[bool, str]:
    from ..engine import flows as FL
    from ..engine import loader
    from ..models import FlowRun

    item = _plan_item(m, "flows", t.target)
    F0 = _read(m)

    def apply(out: dict):
        steps = out.get("steps") or {}
        if isinstance(steps, list):
            steps = {str(s.get("id") or f"s{i + 1}"): {k: v for k, v in s.items() if k != "id"} for i, s in enumerate(steps) if isinstance(s, dict)}
        steps = {_name(k) or f"s{i + 1}": v for i, (k, v) in enumerate(steps.items()) if isinstance(v, dict)}
        probs = [f"flows/{t.target} · {sid}: API steps are not for the team — do it without one" for sid, s in steps.items() if s.get("type") == "api"]
        trig = out.get("trigger") or {"type": item.get("trigger") or "button"}
        if isinstance(trig, str):
            trig = {"type": trig}
        if trig.get("type") == "schedule":
            trig.setdefault("every", "weekday")
            trig.setdefault("at", "07:00")
        run_as = out.get("run_as") if out.get("run_as") in ("starter", "owner") else ("owner" if trig.get("type") in ("schedule", "row_added", "row_changed") else "starter")
        start = _name(out.get("start") or "") or next(iter(steps), "")
        f = {"title": _text(out.get("title"), 60) or item.get("title") or t.target, "enabled": False, "note": f"Built by {ai_name()}'s team (job {m.id}) — off until you switch it on.",
             "trigger": trig, "run_as": run_as, "start": start, "steps": steps}
        from ..engine import rules as RULES

        why = RULES.why_not(f)
        if why:
            probs.append(f"flows/{t.target}: keep to the Builder's rule shape (find rows → for each → if (only yes) → steps): {why}")
        F = _read(m)
        F["flows"][t.target] = f
        td = _write(db, m, F, agent=ag, what=f"flow {t.target}")
        probs += problems_for(td.errors, f"flows/{t.target}")
        t.output = {"files": [f"flows/{t.target}.json"], "steps": len(steps), "trigger": trig.get("type")}
        if probs:
            return False, probs, ""
        _step(t, 2)
        # run it dry: nothing written, sent or called
        owner = _owner(db, m)
        tool = loader.read_folder(draft_dir(m.tool_slug))
        try:
            run_ = FL.start(db, tool, t.target, trigger=trig.get("type") or "button", starter=owner, dry=True, flow=tool.flows.get(t.target))
            dry = {"status": run_.status, "note": run_.note, "log": (run_.log or [])[:30]}
            db.delete(run_)
            db.flush()
        except FL.FlowError as e:
            return False, [f"flows/{t.target}: the dry run stopped: {e}"], ""
        say(db, m, f"Ran “{f['title']}” dry: {dry['status']}" + (f" — {dry['note']}" if dry["note"] else "") + ".", agent=ag, task=t, kind="test", detail={"dry_run": dry})
        if dry["status"] not in ("done", "waiting"):
            return False, [f"flows/{t.target}: the dry run ended {dry['status']}: {dry['note']}"], ""
        t.output["dry_run"] = dry["status"]
        return True, [], f"Wrote the flow “{f['title']}”: {len(steps)} steps, started by {trig.get('type')}; it ran dry to the end. It is switched off until you switch it on."

    return _retrying(db, m, ag, t, "team_flow", "Write this flow for the tool. Follow INPUT.spec exactly; use only the tables in INPUT.tables.",
                     {"flow": item, "tables": outline(F0)["tables"], "providers": [p["name"] for p in catalogue(db)["providers"]], "job": m.brief[:1500], "spec": SPEC_FLOW, "instruction": t.detail,
                      **({"current": F0["flows"].get(t.target)} if F0["flows"].get(t.target) else {})},
                     {"title": "string", "trigger": {}, "run_as": "string", "start": "string", "steps": {}}, apply)


def _norm_page(F: dict, name: str, out: dict, item: dict, notes: list) -> dict:
    other_ids = {str(w.get("id")) for pn, p in F["pages"].items() if pn != name for s in ((p.get("layout") or {}).get("sections") or []) for c in s.get("columns") or [] for w in c.get("widgets") or []}
    secs = out.get("sections") or (out.get("layout") or {}).get("sections") or []
    if not secs and out.get("blocks"):
        secs = [{"columns": [{"width": 12, "blocks": out["blocks"]}]}]
    current = {str(w.get("id")): w for sec in ((F["pages"].get(name) or {}).get("layout") or {}).get("sections") or [] for c in sec.get("columns") or [] for w in c.get("widgets") or []}
    used, n, res = set(), 0, []
    for si, s in enumerate(secs[:6] if isinstance(secs, list) else []):
        if not isinstance(s, dict):
            continue
        cols = []
        for c in (s.get("columns") or [])[:3]:
            if not isinstance(c, dict):
                continue
            ws = []
            for b in (c.get("blocks") or c.get("widgets") or [])[:10]:
                if not isinstance(b, dict):
                    continue
                cur = current.get(str(b.get("id") or ""))
                if cur is not None and (set(b) <= {"id"} or cur.get("type") not in TEAM_BLOCKS):
                    ws.append(dict(cur))  # a block kept as it is (given by its id, or one the team may not write)
                    used.add(str(cur.get("id")))
                    continue
                typ = b.get("type")
                if typ not in TEAM_BLOCKS:
                    notes.append(f"left out a '{typ}' block — the team only uses {', '.join(TEAM_BLOCKS)}")
                    continue
                wid = _name(b.get("id") or "", 36)
                if not wid or wid in used or wid in other_ids:
                    n += 1
                    wid = f"{name[:20]}_{typ}_{n}"
                used.add(wid)
                w = {**b, "id": wid}
                src = w.get("source")
                if isinstance(src, str):
                    src = {"data": src} if src in F["data"] else {"provider": src}
                if isinstance(src, dict) and src.get("api"):
                    notes.append(f"left out the API source on '{wid}' — not for the team")
                    continue
                if src is not None:
                    w["source"] = src
                for fld in ("columns", "fields"):
                    if isinstance(w.get(fld), list):
                        w[fld] = [{"key": _name(x)} if isinstance(x, str) else x for x in w[fld] if isinstance(x, (str, dict))]
                if typ == "table" and isinstance(src, dict) and src.get("data") in F["data"]:
                    ok_f = {c.get("key") for c in F["data"][src["data"]].get("columns") or [] if c.get("type") in ("choice", "link", "bool")}
                    fl = [_name(x) for x in (w.get("filters") or []) if isinstance(x, str) and _name(x) in ok_f]
                    if fl:
                        w["filters"] = fl
                    else:
                        w.pop("filters", None)
                if typ == "metrics" and isinstance(w.get("items"), list):
                    w["items"] = [{"metric": x} if isinstance(x, str) else x for x in w["items"]]
                if typ == "stats" and isinstance(w.get("items"), list):
                    w["items"] = [{"key": x} if isinstance(x, str) else x for x in w["items"]]
                ws.append(w)
            try:
                width = max(1, min(12, int(c.get("width") or 12)))
            except (TypeError, ValueError):
                width = 12
            cols.append({"width": width, "widgets": ws})
        if cols and sum(c["width"] for c in cols) > 12:
            for c in cols:
                c["width"] = 12 // len(cols)
        if cols:
            res.append({"id": f"s{si + 1}", "columns": cols})
    width = out.get("width") if out.get("width") in ("narrow", "normal", "wide") else "normal"
    page = {"title": _text(out.get("title"), 80) or item.get("title") or name, "menu": True, "order": [p.get("name") for p in (F.get("_plan_pages") or [])].index(name) + 1 if name in [p.get("name") for p in (F.get("_plan_pages") or [])] else 10,
            "style": {"width": width}, "layout": {"sections": res}}
    if out.get("intro"):
        page["intro"] = _text(out.get("intro"), 200)
    # a page for some roles only: as the plan said, else as the page already was (a change keeps it)
    proles = [r for r in ((item or {}).get("roles") or ((F["pages"].get(name) or {}).get("access") or {}).get("roles") or []) if r in ("owner", "admin", "manager", "user")]
    if proles:
        page["access"] = {"roles": proles}
    return page


def w_page(db: Session, m: Mission, ag: MissionAgent, t: MissionTask, *, fix: Optional[dict] = None) -> tuple[bool, str]:
    name = t.target.split(":", 1)[1] if fix is not None and ":" in t.target else t.target
    item = _plan_item(m, "pages", name)
    F0 = _read(m)
    cat = catalogue(db)
    flows_btn = [n for n, f in F0["flows"].items() if (f.get("trigger") or {}).get("type") == "button"]
    payload = {"page": {"name": name, **item}, "tool": {"slug": m.tool_slug, "title": (m.plan or {}).get("title")}, "draft": outline(F0),
               "button_flows": flows_btn, "table_providers": [p for p in cat["providers"] if p["kind"] == "table"],
               "numbers": [f"{x['key']}: {x['label']}" for x in cat["numbers"]], "job": m.brief[:1200], "spec": SPEC_PAGE, "look": DESIGN_RULES, "instruction": t.detail}
    if fix is not None:
        payload["current_page"] = outline(F0)["pages"].get(name)
        payload["what_to_change"] = fix
    notes: list[str] = []

    def apply(out: dict):
        F = _read(m)
        F["_plan_pages"] = (m.plan or {}).get("pages") or []
        page = _norm_page(F, name, out, item, notes)
        F.pop("_plan_pages", None)
        F["pages"][name] = page
        if not F["manifest"].get("home") or F["manifest"]["home"] not in F["pages"]:
            F["manifest"]["home"] = name
        td = _write(db, m, F, agent=ag, what=f"page {name}")
        probs = problems_for(td.errors, f"pages/{name}")
        probs += [e for e in td.errors if e.startswith("languages/en.json") and name in e]
        n = sum(len(c["widgets"]) for s in page["layout"]["sections"] for c in s["columns"])
        t.output = {"files": [f"pages/{name}.json"], "blocks": n, "notes": notes[:6]}
        if n == 0:
            probs.append(f"pages/{name}: the page has no blocks")
        return not probs, probs, (f"{'Changed' if fix is not None else 'Laid out'} the page “{_say_words(F, page['title'])}”: {n} blocks" + (f" ({'; '.join(notes[:2])})" if notes else "") + ".")

    instr = ("Change this page as INPUT.what_to_change says; return the whole page. " if fix is not None else "Lay out this page of the tool. ") + \
        "Follow INPUT.spec for the blocks and INPUT.look for how it must look. To keep a block of the current page as it is (and always for blocks you cannot write: grid, image, code, embed), give only its id: {\"id\": \"...\"}. Use only the tables in INPUT.draft.tables, the providers in INPUT.table_providers, the numbers in INPUT.numbers and the flows in INPUT.button_flows."
    return _retrying(db, m, ag, t, "team_fix" if fix is not None else "team_page", instr, payload,
                     {"title": "string", "intro": "string", "width": "string", "sections": [{"columns": [{"width": 12, "blocks": [{}]}]}]}, apply)


def w_fix(db: Session, m: Mission, ag: MissionAgent, t: MissionTask) -> tuple[bool, str]:
    kind, _, name = t.target.partition(":")
    if kind == "page":
        return w_page(db, m, ag, t, fix={"problems": (t.output or {}).get("problems") or [t.detail], "fixes": (t.output or {}).get("fixes") or []})
    if kind == "data":
        t.target = name
        return w_data(db, m, ag, t)
    if kind == "flow":
        t.target = name
        return w_flow(db, m, ag, t)
    return False, f"don't know how to fix a {kind}"


def loader_opt(v) -> str:
    from ..engine.loader import opt_slug

    return opt_slug(v)


def _fake(col: dict, i: int, table: str) -> Any:
    k, ty = col.get("key", ""), col.get("type", "text")
    if ty == "choice" and col.get("options"):
        return col["options"][i % len(col["options"])]
    if ty == "link":
        return (i % 5) + 1
    if ty == "file":
        return {"name": f"{k.replace('_', '-')}-{i + 1}.pdf", "size": 48_000 + i * 1300, "type": "pdf"}
    names = ["Thandi Mokoena", "Pieter van Wyk", "Aisha Patel", "Sipho Dlamini", "Lerato Nkosi", "Johan Smit"]
    if ty == "date":
        from datetime import date, timedelta

        return (date.today() + timedelta(days=(i * 9) - 12)).isoformat()
    if ty == "int":
        return (i + 1) * 3
    if ty == "number":
        return round(1250.5 * (i + 1), 2)
    if ty == "bool":
        return i % 2 == 0
    if "email" in k:
        return names[i % len(names)].split()[0].lower() + "@example.com"
    if any(w in k for w in ("owner", "person", "name_of", "assignee", "who", "responsible", "member")):
        return names[i % len(names)]
    if "status" in k:
        return ["On track", "At risk", "Late", "Done", "Not started"][i % 5]
    if k in ("name", "title") or k.endswith(("_name", "_title")):
        return f"{table.replace('_', ' ').rstrip('s').capitalize()} {chr(65 + i)}"
    return f"{k.replace('_', ' ').capitalize()} {i + 1}"


def w_sample(db: Session, m: Mission, ag: MissionAgent, t: MissionTask) -> tuple[bool, str]:
    F = _read(m)
    tables = {n: [c for c in (b.get("columns") or []) if isinstance(c, dict)] for n, b in F["data"].items()}
    rows: dict[str, list] = {}
    if tables:
        try:
            out = _ask(db, m, ag, t, "team_sample", "Make up 5 believable rows for every table, for screenshots only. Invent names and values; never real people or real accounts.",
                       {"tables": {n: [f"{c.get('key')}:{c.get('type', 'text')}" for c in cols] for n, cols in tables.items()}, "tool": (m.plan or {}).get("title")}, {"rows": {}})
            got = out.get("rows") if isinstance(out.get("rows"), dict) else {}
        except ModelError:
            got = {}
        for n, cols in tables.items():
            keys = {c["key"] for c in cols}
            good = [{k: v for k, v in r.items() if k in keys} for r in (got.get(n) or []) if isinstance(r, dict)][:6]
            rows[n] = good if len(good) >= 3 else [{c["key"]: _fake(c, i, n) for c in cols} for i in range(5)]
            # a made-up choice must be one of the options, a link a row number, a file a record — else the engine's own
            for i, r in enumerate(rows[n]):
                for c in cols:
                    v = r.get(c["key"])
                    if (c.get("type") == "choice" and v not in (c.get("options") or [])) or (c.get("type") == "link" and not isinstance(v, int)) or (c.get("type") == "file" and not isinstance(v, dict)):
                        r[c["key"]] = _fake(c, i, n)
            for i, r in enumerate(rows[n]):
                r.setdefault("id", i + 1)
    t.output = {"rows": rows}
    return True, "Made up " + ", ".join(f"{len(v)} {n.replace('_', ' ')} rows" for n, v in rows.items()) + " for the screenshots (not real data)." if rows else "There are no tables to make rows for."


def _sample_rows(db: Session, m: Mission) -> dict:
    t = db.query(MissionTask).filter(MissionTask.mission_id == m.id, MissionTask.kind == "sample", MissionTask.status == "done").order_by(MissionTask.id.desc()).first()
    return ((t.output or {}).get("rows") or {}) if t else {}


def shots_dir(m: Mission) -> Path:
    d = settings.data_dir / "cexa" / "jobs" / str(m.id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def w_look(db: Session, m: Mission, ag: MissionAgent, t: MissionTask) -> tuple[bool, str]:
    from . import eyes

    name = t.target
    F = _read(m)
    if name not in F["pages"]:
        return False, f"there is no page '{name}' to look at"
    rows = _sample_rows(db, m)
    png, why = eyes.page_png(F, name, rows)
    shot = ""
    if png:
        shot = f"{name}-r{m.round}-{t.id}.png"
        (shots_dir(m) / shot).write_bytes(png)
        say(db, m, f"Took a screenshot of “{_say_words(F, F['pages'][name].get('title'))}” (made-up rows).", agent=ag, task=t, kind="look", detail={"shot": shot})
    else:
        say(db, m, f"No screenshot ({why}) — reading the layout instead.", agent=ag, task=t, kind="look")
    _step(t, 0)
    ref, _ = eyes.reference_png()
    images = [x for x in (ref, png) if x] if png else None
    out = _ask(db, m, ag, t, "team_look",
               ("The first picture is a page of CBX-Connect as it looks today; the second is the new page. " if images and len(images) == 2 else "The picture is the new page. " if images else "")
               + "Judge the new page against INPUT.rules: does it look and read like the rest of CBX-Connect, and does it do its job (INPUT.purpose)? "
               "Score it 0 to 100. List what is good, what is wrong, and for each wrong thing the change to make (which block, what to do).",
               {"page": outline(F)["pages"].get(name), "purpose": _plan_item(m, "pages", name).get("purpose", ""), "rules": DESIGN_RULES},
               {"score": 0, "verdict": "string", "good": ["string"], "problems": ["string"], "fixes": [{"block": "string", "change": "string"}]}, images=images)
    _step(t, 2)
    try:
        score = max(0, min(100, int(float(out.get("score") or 0))))
    except (TypeError, ValueError):
        score = 0
    problems = [p for p in (out.get("problems") or []) if isinstance(p, str)][:8]
    fixes = [f for f in (out.get("fixes") or []) if isinstance(f, dict)][:8]
    t.output = {"score": score, "shot": shot, "verdict": _text(out.get("verdict"), 400), "good": [g for g in (out.get("good") or []) if isinstance(g, str)][:6], "problems": problems, "fixes": fixes}
    say(db, m, f"“{_say_words(F, F['pages'][name].get('title'))}”: {score}/100 — {_text(out.get('verdict'), 300)}", agent=ag, task=t, kind="look", detail={"shot": shot, **t.output})
    relooked = t.parent_id is not None or ":r" in t.key or t.key.endswith(":again")
    if score < LOOK_PASS and (problems or fixes) and not relooked:
        page_task = db.query(MissionTask).filter(MissionTask.mission_id == m.id, MissionTask.kind.in_(["page", "fix"]), MissionTask.target.in_([name, f"page:{name}"])).order_by(MissionTask.id).first()
        dev = db.get(MissionAgent, page_task.agent_id) if page_task and page_task.agent_id else ag
        agents = {a.name: a for a in db.query(MissionAgent).filter(MissionAgent.mission_id == m.id).all()}
        fx = _add_tasks(db, m, [{"key": f"fix:{name}:{t.id}", "kind": "fix", "target": f"page:{name}", "agent": dev.name, "title": f"Fix “{name.replace('_', ' ')}” as {ag.name} asked", "detail": "; ".join(problems[:4]), "depends": []}], agents, round_no=m.round, parent=t)[0]
        fx.output = {"problems": problems, "fixes": fixes}
        again = _add_tasks(db, m, [{"key": f"look:{name}:{t.id}:again", "kind": "look", "target": name, "agent": ag.name, "title": f"Look at “{name.replace('_', ' ')}” again", "detail": "", "depends": [fx.key]}], agents, round_no=m.round, parent=t)[0]
        # what waited on this look now waits on the second look
        for other in db.query(MissionTask).filter(MissionTask.mission_id == m.id, MissionTask.status == "todo").all():
            if t.key in (other.depends or []) and other.id not in (fx.id, again.id):
                other.depends = list(other.depends) + [again.key]
        say(db, m, f"{score} is under {LOOK_PASS}: {dev.name}, please fix it — then I'll look again.", agent=ag, task=t)
    return True, f"Looked at “{_say_words(F, F['pages'][name].get('title'))}”: {score}/100."


def w_words(db: Session, m: Mission, ag: MissionAgent, t: MissionTask) -> tuple[bool, str]:
    names = {"ar": "Arabic", "pt": "Portuguese (Portugal)", "fr": "French"}
    done = []
    for i, (lang, lname) in enumerate(names.items()):
        F = _read(m)
        en = F["languages"].get("en") or {}
        have = F["languages"].get(lang) or {}
        todo = {k: v for k, v in en.items() if not have.get(k)}
        if not todo:
            _step(t, i)
            continue
        got: dict = {}
        items = list(todo.items())
        for j in range(0, len(items), 60):  # 60 words a call keeps the reply short
            chunk = dict(items[j:j + 60])
            out = _ask(db, m, ag, t, "team_words", f"Translate every value into {lname} for business users. Keep the keys exactly; keep {{{{templates}}}} and ** as they are. Short, plain words.",
                       {"language": lang, "words": chunk}, {"words": {}})
            w = out.get("words") if isinstance(out.get("words"), dict) else {}
            got.update({k: v.strip() for k, v in w.items() if k in chunk and isinstance(v, str) and v.strip()})
        F = _read(m)
        F["languages"].setdefault(lang, {}).update(got)
        _write(db, m, F, agent=ag, what=f"{lname}: {len(got)} words")
        done.append(f"{lname} {len(got)}/{len(todo)}")
        _step(t, i)
        db.commit()
    return True, ("Translated: " + ", ".join(done) + ".") if done else "Every word was already there in all four languages."


def w_readme(db: Session, m: Mission, ag: MissionAgent, t: MissionTask) -> tuple[bool, str]:
    F = _read(m)
    out = _ask(db, m, ag, t, "team_readme", "Write the tool's README.md (markdown, under 60 lines): what it is and who it is for; each page and what it shows; each table and its columns; "
               "each flow, when it runs and that it ships switched off; who owns it; how to change it in the Builder. Plain words.",
               {"tool": (m.plan or {}).get("title"), "summary": (m.plan or {}).get("summary"), "draft": outline(F), "owner": F["manifest"].get("owner"), "job": m.brief[:1000]}, {"readme": "string"})
    text = out.get("readme") if isinstance(out.get("readme"), str) else ""
    if len(text.strip()) < 40:
        return False, "the README came back empty"
    F = _read(m)
    F["readme"] = text.strip()[:12000] + f"\n\n_Built by {ai_name()}'s team (job {m.id}); reviewed and released by a person._\n"
    _write(db, m, F, agent=ag, what="README")
    t.output = {"files": ["README.md"], "lines": text.count("\n") + 1}
    return True, f"Wrote the README ({text.count(chr(10)) + 1} lines)."


def w_test(db: Session, m: Mission, ag: MissionAgent, t: MissionTask) -> tuple[bool, str]:
    """No model: the tester runs the same checks the Builder's release does — the folder loads, every page
    draws (as the owner, the draft's own data), every flow runs dry — and counts the words."""
    from ..api.engine_api import page_out, widget_rows
    from ..engine import flows as FL
    from ..engine import loader
    from ..engine.loader import widgets_of
    from fastapi import HTTPException

    tool = loader.read_folder(draft_dir(m.tool_slug))
    owner = _owner(db, m)
    res = {"errors": list(tool.errors), "pages": {}, "flows": {}, "missing": {k: len(v) for k, v in tool.missing().items()}}
    _step(t, 0)
    for name, p in tool.pages.items():
        try:
            page_out(tool, name, p, owner, "en", all_pages=True)
            bad = []
            for w in widgets_of(name, p):
                src = w.get("source") or {}
                if src:
                    try:
                        widget_rows(db, owner, tool, w, src, {}, limit=5)
                    except HTTPException as e:
                        bad.append(f"{w.get('id')}: {e.detail}")
            res["pages"][name] = "ok" if not bad else "; ".join(bad)
        except Exception as e:  # noqa: BLE001 — a page that does not draw is the finding
            res["pages"][name] = f"{type(e).__name__}: {e}"
    _step(t, 1)
    for name, f in tool.flows.items():
        try:
            r = FL.start(db, tool, name, trigger=(f.get("trigger") or {}).get("type") or "button", starter=owner, dry=True, flow=f)
            res["flows"][name] = r.status + (f" — {r.note}" if r.note and r.status not in ("done", "waiting") else "")
            from ..engine import rules as RULES

            why = RULES.why_not(f)
            if why:
                res["flows"][name] = f"not a rule the Builder can open: {why}"
            db.delete(r)
            db.flush()
        except Exception as e:  # noqa: BLE001
            res["flows"][name] = f"{type(e).__name__}: {e}"
    _step(t, 3)
    bad_pages = {k: v for k, v in res["pages"].items() if v != "ok"}
    bad_flows = {k: v for k, v in res["flows"].items() if not v.startswith(("done", "waiting"))}
    t.output = res
    say(db, m, f"Folder: {'loads clean' if not res['errors'] else str(len(res['errors'])) + ' problems'} · pages: {len(res['pages']) - len(bad_pages)}/{len(res['pages'])} draw · flows: {len(res['flows']) - len(bad_flows)}/{len(res['flows'])} run dry · "
        + "words missing: " + ", ".join(f"{k} {v}" for k, v in res["missing"].items()), agent=ag, task=t, kind="test", detail=res)
    ok = not res["errors"] and not bad_pages and not bad_flows
    if not ok and not any(x.kind == "fix" and x.key.startswith("fix:test") for x in db.query(MissionTask).filter(MissionTask.mission_id == m.id).all()):
        # one round of repairs, handed back to the developers, then test again
        agents = {a.name: a for a in db.query(MissionAgent).filter(MissionAgent.mission_id == m.id).all()}
        devs = [a.name for a in agents.values() if a.role == "developer"] or [ag.name]
        parts = set()
        for e in res["errors"]:
            mm = re.match(r"(pages|data|flows)/([a-z0-9_]+)", e)
            if mm:
                parts.add(({"pages": "page", "data": "data", "flows": "flow"}[mm.group(1)], mm.group(2)))
        parts |= {("page", k) for k in bad_pages} | {("flow", k) for k in bad_flows}
        new = []
        for kind, name in sorted(parts):
            probs = [e for e in res["errors"] if f"/{name}" in e] + ([bad_pages[name]] if kind == "page" and name in bad_pages else []) + ([bad_flows[name]] if kind == "flow" and name in bad_flows else [])
            new.append({"key": f"fix:test:{kind}:{name}", "kind": "fix", "target": f"{kind}:{name}", "agent": devs[-1] if kind == "page" else devs[0], "title": f"Fix the {kind} “{name.replace('_', ' ')}” (the test found problems)", "detail": "; ".join(probs[:4]), "depends": []})
        if new:
            rows = _add_tasks(db, m, new, agents, round_no=m.round, parent=t)
            for r_, n_ in zip(rows, new):
                r_.output = {"problems": n_["detail"].split("; ")}
            again = _add_tasks(db, m, [{"key": f"test:again:{t.id}", "kind": "test", "target": "", "agent": ag.name, "title": "Test the draft again", "detail": "", "depends": [x["key"] for x in new]}], agents, round_no=m.round, parent=t)[0]
            for other in db.query(MissionTask).filter(MissionTask.mission_id == m.id, MissionTask.status == "todo").all():
                if t.key in (other.depends or []) and other.id != again.id and other.id not in [r_.id for r_ in rows]:
                    other.depends = list(other.depends) + [again.key]
            say(db, m, f"Handing {len(new)} fix{'es' if len(new) != 1 else ''} back to the developers, then I test again.", agent=ag, task=t)
            return True, "The test found problems; fixes are on the board."
    return ok, ("Everything passes: the folder loads clean, every page draws, every flow runs dry." if ok else "Still failing after the fixes: " + "; ".join((res["errors"] + list(bad_pages.values()) + list(bad_flows.values()))[:4]))


def w_code(db: Session, m: Mission, ag: MissionAgent, t: MissionTask) -> tuple[bool, str]:
    """A core change goes through C-EXA-as-developer's sandbox: a proposal, built on its own branch in a
    copy of the app, the full test suite run there. The owner's Go on the plan is the go for the build;
    shipping it is still the owner's, from C-EXA as developer."""
    from . import developer

    owner = _owner(db, m)
    p = Proposal(title=t.title[:200], evidence_kind="job", evidence=f"{ai_name()} job {m.id}: {m.brief[:1500]}", body=(t.detail + (f"\n\nOwner's notes: " + " / ".join(_notes(m)) if _notes(m) else ""))[:4000], priority=60, status="noticed")
    db.add(p)
    db.flush()
    t.output = {"proposal_id": p.id}
    _step(t, 0)
    try:
        developer.go(db, p, owner)
    except ValueError as e:
        return False, f"The sandbox build could not start: {e}"
    t.status = "testing"
    say(db, m, f"Building “{t.title}” in a sandbox copy of the app; the full test suite runs there.", agent=ag, task=t, kind="write", detail={"proposal": p.id})
    return True, ""


def _poll_code(db: Session, m: Mission) -> None:
    for t in db.query(MissionTask).filter(MissionTask.mission_id == m.id, MissionTask.kind == "code", MissionTask.status == "testing").all():
        p = db.get(Proposal, (t.output or {}).get("proposal_id") or 0)
        ag = db.get(MissionAgent, t.agent_id) if t.agent_id else None
        if p is None:
            t.status = "problem"
            continue
        tests = p.tests or {}
        if p.status == "signoff":
            t.status, sentence, kind = "done", f"“{t.title}” is built and green in {p.sandbox}: {tests.get('passed', 0)} tests passed. It waits for you on C-EXA as developer (ship it from there).", "done"
        elif p.status == "building" and tests and not tests.get("ok"):
            t.status, sentence, kind = "problem", f"“{t.title}”: the tests are red in the sandbox ({tests.get('failed', 0)} failed) after {len(tests.get('rounds') or [])} rounds.", "problem"
        elif p.status == "noticed" and tests:
            t.status, sentence, kind = "problem", f"“{t.title}”: the sandbox build failed — {str(tests.get('output') or '')[:200]}", "problem"
        elif p.status == "declined":
            t.status, sentence, kind = "skipped", f"“{t.title}”: you said not now.", "said"
        else:
            continue
        t.finished_at = utcnow()
        t.output = {**(t.output or {}), "tests": {k: v for k, v in tests.items() if k != "output"}, "diff": (p.diff or "")[:6000]}
        _step(t, len(t.steps or []))
        say(db, m, sentence, agent=ag, task=t, kind=kind)
        _set(ag, "done", "")


def w_summary(db: Session, m: Mission, ag: MissionAgent, t: MissionTask) -> tuple[bool, str]:
    lead = _lead(db, m)
    tasks = db.query(MissionTask).filter(MissionTask.mission_id == m.id, MissionTask.id != t.id).order_by(MissionTask.order).all()
    F = _read(m)
    facts = {"tool": (m.plan or {}).get("title"), "slug": m.tool_slug, "round": m.round, "draft": outline(F),
             "tasks": [{"title": x.title, "status": x.status, "result": (x.output or {}).get("problem") or (f"score {x.output.get('score')}" if x.kind == "look" and x.output else "")} for x in tasks],
             "left_out": (m.plan or {}).get("left_out", ""), "owner_notes": _notes(m)}
    try:
        out = _ask(db, m, lead, t, "ceo_summary", "Write the owner a short report on the job: what the team built, what works, what did not and why, what you left out, and what he should do next "
                   "(open it in the Builder, try it in the preview, switch the flows on when ready, release it). Under 150 words, plain sentences, no headings.",
                   facts, {"summary": "string", "next": ["string"]})
        text = _text(out.get("summary"), 3000)
        nxt = [n for n in (out.get("next") or []) if isinstance(n, str)][:5]
    except ModelError:
        text, nxt = "", []
    probs = [x for x in tasks if x.status == "problem"]
    if not text:
        text = f"The team built {m.tool_slug}: {len(F['data'])} table(s), {len(F['pages'])} page(s), {len(F['flows'])} flow(s)." + (f" {len(probs)} task(s) need you." if probs else " Everything passed.")
    m.summary = text + ("\n\nNext:\n\n" + "\n".join(f"- {n}" for n in nxt) if nxt else "")
    return True, "Written up."


def _finish(db: Session, m: Mission) -> None:
    lead = _lead(db, m)
    tasks = db.query(MissionTask).filter(MissionTask.mission_id == m.id).all()
    probs = [x for x in tasks if x.status == "problem"]
    if m.kind in ("report", "review", "advice"):
        m.status = "review" if m.kind == "advice" else "done"
        m.finished_at = utcnow() if m.status == "done" else None
        m.state = m.summary or "Done."
        say(db, m, m.state, agent=lead, kind="done")
        for a in db.query(MissionAgent).filter(MissionAgent.mission_id == m.id).all():
            _set(a, "done", "")
        if m.kind == "advice":
            _set(lead, "waiting", "waiting for you to read it")
            _notify(db, m, "C-EXA's answer is ready", f"“{m.title}”: the document is ready to read.")
        log(db, "cexa_job_done", actor=_cexa(db), actor_label=f"axl→team:{m.team_key}", target=f"job:{m.id}", detail=f"{m.kind}: {m.summary[:180]}; {m.calls} model calls")
        return
    m.status = "review"
    m.state = ("Done — ready for you to look at." if not probs else f"Done, with {len(probs)} thing{'s' if len(probs) != 1 else ''} for you to look at.") + " Open it in the Builder; nothing is live until you release it."
    say(db, m, m.summary or m.state, agent=lead, kind="done")
    for a in db.query(MissionAgent).filter(MissionAgent.mission_id == m.id).all():
        _set(a, "done", "")
    _set(lead, "waiting", "waiting for you to look")
    _notify(db, m, "C-EXA's team has finished", f"“{m.title}”: {m.state}")
    log(db, "cexa_job_review", actor=_cexa(db), actor_label="axl→team", target=f"job:{m.id}", detail=f"{len(tasks)} tasks, {len(probs)} problems, {m.calls} model calls")


WORKERS = {"data": w_data, "flow": w_flow, "page": w_page, "fix": w_fix, "sample": w_sample, "look": w_look, "words": w_words, "readme": w_readme, "test": w_test, "code": w_code, "summary": w_summary}


# ---- out ---------------------------------------------------------------------------------------------------

EVENT_LIGHT = ("shot", "score", "questions", "problems", "dry_run", "proposal", "note", "changes", "change_plan", "by", "verdict", "fixes", "good")


def mission_out(db: Session, m: Mission, *, full: bool = False) -> dict:
    tasks = db.query(MissionTask).filter(MissionTask.mission_id == m.id).order_by(MissionTask.order).all()
    agents = db.query(MissionAgent).filter(MissionAgent.mission_id == m.id).order_by(MissionAgent.id).all()
    d = {"id": m.id, "title": m.title, "brief": m.brief, "status": m.status, "state": m.state, "paused": m.paused, "round": m.round, "tool_slug": m.tool_slug,
         "calls": m.calls, "max_calls": m.max_calls, "created_at": m.created_at.isoformat() if m.created_at else None, "started_at": m.started_at.isoformat() if m.started_at else None,
         "finished_at": m.finished_at.isoformat() if m.finished_at else None, "running": m.id in _RUNNING,
         "counts": {"tasks": len(tasks), "done": sum(1 for t in tasks if t.status == "done"), "problems": sum(1 for t in tasks if t.status == "problem"), "working": sum(1 for t in tasks if t.status in ("working", "testing"))},
         "agents_n": len(agents)}
    d["team_key"], d["kind"] = m.team_key or "", m.kind or ""
    if not full:
        d["team"] = [{"name": a.name, "role": a.role, "status": a.status} for a in agents]
        d["team_name"] = (_team_info(db, m) or {}).get("name", "")
        return d
    names = {a.id: a.name for a in agents}
    d.update({
        "questions": m.questions or [], "plan": {k: v for k, v in (m.plan or {}).items() if not k.startswith("_")}, "summary": m.summary, "notes": m.notes or [],
        "team": _team_info(db, m), "kind": m.kind or "build", "document": m.document or "", "findings": (m.plan or {}).get("findings") or [], "verdict": (m.plan or {}).get("verdict") or "",
        "agents": [{"id": a.id, "name": a.name, "role": a.role, "role_label": role_label(a.role), "focus": a.focus, "model": a.model, "status": a.status, "doing": a.doing, "task_id": a.task_id, "calls": a.calls,
                    "tasks": sum(1 for t in tasks if t.agent_id == a.id), "done": sum(1 for t in tasks if t.agent_id == a.id and t.status == "done")} for a in agents],
        "tasks": [{"id": t.id, "key": t.key, "kind": t.kind, "title": t.title, "detail": t.detail, "status": t.status, "agent": names.get(t.agent_id, ""), "agent_id": t.agent_id, "parent_id": t.parent_id,
                   "steps": t.steps or [], "attempts": t.attempts, "round": t.round, "depends": t.depends or [], "output": _light_output(t)} for t in tasks],
        "events": [{"id": e.id, "at": e.at.isoformat() if e.at else None, "kind": e.kind, "text": e.text, "agent": names.get(e.agent_id, ""), "task_id": e.task_id,
                    "detail": {k: v for k, v in (e.detail or {}).items() if k in EVENT_LIGHT}, "has_prompt": bool((e.detail or {}).get("user"))}
                   for e in db.query(MissionEvent).filter(MissionEvent.mission_id == m.id).order_by(MissionEvent.id.desc()).limit(400).all()],
        "draft": _draft_info(m), "roles": {k: {"label": v[0], "what": v[1], "model": model_for_role(k)} for k, v in ROLE_INFO.items()},
    })
    return d


def _team_info(db: Session, m: Mission) -> Optional[dict]:
    from ..models import AgentTeam

    t = db.query(AgentTeam).filter(AgentTeam.key == m.team_key).first() if m.team_key else None
    return {"key": t.key, "name": t.name, "word": t.word} if t else None


def _light_output(t: MissionTask) -> dict:
    o = dict(t.output or {})
    o.pop("rows", None)
    if "diff" in o:
        o["diff"] = o["diff"][:4000]
    return o


def _draft_info(m: Mission) -> dict:
    if not m.tool_slug:
        return {"exists": False}
    d = draft_dir(m.tool_slug)
    if not (d / "tool.json").exists():
        return {"exists": False, "slug": m.tool_slug}
    from ..engine import loader

    try:
        with _DRAFT_LOCK:
            t = loader.read_folder(d)
    except OSError:
        return {"exists": True, "slug": m.tool_slug, "busy": True, "errors": [], "files": []}
    files = sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file() and not p.name.startswith(".") and p.suffix in (".json", ".md"))
    return {"exists": True, "slug": m.tool_slug, "errors": t.errors[:20], "warnings": t.warnings[:10], "files": files, "pages": list(t.pages), "data": list(t.data), "flows": list(t.flows), "released": (d / ".released").exists()}


from . import teams as _teams  # noqa: E402,F401 — registers the standing teams' workers and their schedule
