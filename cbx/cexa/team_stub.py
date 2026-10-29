"""Stub answers for C-EXA's team (model mode `stub`: tests, a fresh install with no GPU). Every prompt the
team sends ends with an INPUT block (JSON); these answers are built from it, so a job runs from the brief
to a sound Builder draft without a model — and the tests can check every step of the pipeline."""
from __future__ import annotations

import json
import re


def _input(user: str) -> dict:
    m = re.search(r"INPUT:\n(.*)\nEND INPUT", user, flags=re.S)
    try:
        return json.loads(m.group(1)) if m else {}
    except json.JSONDecodeError:
        return {}


def answer(task: str, user: str) -> str:
    d = _input(user)
    fn = globals().get("_" + task)
    return json.dumps(fn(d) if fn else {}, ensure_ascii=False)


def _is_projects(text: str) -> bool:
    return bool(re.search(r"project|gantt|milestone", text or "", flags=re.I))


def _route(job: str, chosen: str) -> str:
    if chosen:
        return chosen
    j = job.lower()
    if re.search(r"\b(list|recommend|advice|advise|structure|document|policy|explain|compare)\b", j) and not re.search(r"\b(build|tool|page)\b", j):
        return "advice"
    if re.search(r"\b(change|improve|update|add .* to)\b", j) and re.search(r"entity compliance|entity-compliance", j):
        return "change"
    if re.search(r"\bsecurity check\b|\bcheck security\b", j):
        return "security"
    return "build"


def _ceo_intake(d: dict) -> dict:
    job = d.get("job", "")
    team = _route(job, d.get("team") or "")
    title = "Project Manager" if _is_projects(job) else (job.split("\n")[0][:40].strip().rstrip(".") or "New tool")
    qs = ["Who will use it — everyone, or managers and admins only?", "When something is late, who should hear about it: the person, you, or both?"] if team == "build" else \
         ["Who is it for — you, the board, or the team?"] if team == "advice" else []
    return {"team": team, "tool": "entity-compliance" if team == "change" else "", "title": title, "understood": f"You want this: {job[:160].strip()}", "questions": qs}


def _ceo_change(d: dict) -> dict:
    pages = list(((d.get("tool") or {}).get("pages") or {}).keys())
    return {"title": "Change Entity Compliance", "summary": "One change to the first page, as asked.", "changes": [{"kind": "page", "name": pages[0] if pages else "main", "instruction": (d.get("job") or "")[:160]}], "left_out": ""}


def _team_analyse(d: dict) -> dict:
    flags = (d.get("facts") or {}).get("flags") or []
    return {"findings": [{"title": f, "severity": "medium" if i == 0 else "low", "evidence": f, "suggestion": "Look at it.", "team": ""} for i, f in enumerate(flags[:8])]}


def _team_check(d: dict) -> dict:
    return {"verdicts": [{"id": f.get("id"), "keep": True, "severity": f.get("severity"), "note": "Supported by the facts."} for f in d.get("findings") or []], "missed": []}


def _team_write(d: dict) -> dict:
    f = d.get("findings") or []
    return {"report": f"## {d.get('team')}\n\n" + (f"{len(f)} things worth a look.\n\n" + "\n".join(f"- **{x.get('severity')}** {x.get('title')}" for x in f) if f else "All in order.")}


def _team_outline(d: dict) -> dict:
    return {"title": "The recommendation", "form": "a list", "sections": [{"heading": "The list", "covers": "what was asked"}, {"heading": "Rules it follows", "covers": "policies and security"}], "must": ["a list"]}


def _team_draft(d: dict) -> dict:
    return {"markdown": "# The recommendation\n\n## The list\n\n- One\n- Two\n- Three\n\n## Rules it follows\n\n- Change control\n- Least privilege\n"}


def _team_acheck(d: dict) -> dict:
    return {"verdict": "Answers the question.", "problems": [{"problem": "Say who owns each item.", "fix": "Add an owner to every line."}]}


def _team_final(d: dict) -> dict:
    notes = " ".join(d.get("owner_notes") or [])
    return {"markdown": "# The recommendation\n\n## The list\n\n- One — owner: the lead\n- Two — owner: the lead\n- Three — owner: the lead\n\n## Rules it follows\n\n- Change control\n- Least privilege\n" + (f"\n_Changed as asked: {notes}_\n" if notes else "")}


def _team_lens(d: dict) -> dict:
    files = d.get("files") or []
    return {"verdict": "pass", "findings": ([{"file": files[0], "line": 1, "severity": "low", "problem": "A comment could be clearer.", "fix": "Reword it."}] if files else [])}


def _team_debate(d: dict) -> dict:
    return {"responses": [{"id": f.get("id"), "agree": True, "why": "Fair."} for f in d.get("their_findings") or []], "verdict": d.get("your_verdict") or "pass"}


def _team_verdict(d: dict) -> dict:
    return {"explanation": f"The council's verdict is {d.get('verdict')}.", "questions": []}


def _ceo_plan(d: dict) -> dict:
    job = d.get("job", "")
    change = d.get("owner_says_change", "")
    core = bool(re.search(r"\bcore\b|new block|timeline block", job + " " + change, flags=re.I))
    if _is_projects(job):
        plan = {
            "title": "Project Manager", "summary": "A small project tool: projects and their tasks, a page for each, a button that reminds people of late tasks, and a morning check that tells you what slipped.",
            "icon": "board", "section": "My tools", "roles": ["owner", "admin", "manager", "user"],
            "data": [{"name": "tasks", "purpose": "The steps of each project, with who and when.", "columns": ["project:link(projects)", "task:text", "assignee:text", "assignee_email:text", "due:date", "status:choice(Not started|In progress|Late|Done)", "evidence:file"]},
                     {"name": "projects", "purpose": "One row per project.", "columns": ["name:text", "owner:text", "start:date", "due:date", "status:choice(On track|At risk|Late|Done)"]}],
            "pages": [{"name": "projects", "title": "Projects", "purpose": "Every project and how it stands.", "blocks": "numbers, the projects table with search, a form to add a project beside it"},
                      {"name": "tasks", "title": "Tasks", "purpose": "Every task, who has it, when it is due.", "blocks": "the tasks table, a form to add a task, a button to send reminders"}],
            "flows": [{"name": "remind_late", "title": "Remind late tasks", "purpose": "Tell the assignee of every late task.", "trigger": "button"},
                      {"name": "morning_check", "title": "Morning check", "purpose": "Each weekday morning, tell the owner how many tasks are late.", "trigger": "schedule"}],
            "code": [{"title": "Add a Timeline block (a Gantt chart of rows with start and due dates)", "purpose": "A new block type the Builder can place.", "why": "The Builder has no timeline."}] if core else [],
            "team": [{"name": "Dev-1", "role": "developer", "focus": "tables and flows"}, {"name": "Dev-2", "role": "developer", "focus": "pages"},
                     {"name": "Iris", "role": "designer", "focus": "the look"}, {"name": "Tess", "role": "tester", "focus": "checks"}, {"name": "Hal", "role": "helper", "focus": "words and rows"}]
                    + ([{"name": "Core-1", "role": "core", "focus": "the Timeline block"}] if core else []),
            "left_out": "A Gantt chart: the Builder has no timeline block yet" + (" — planned as core code." if core else " — ask for it as core code if you want it."),
        }
    else:
        title = (job.split("\n")[0][:40].strip().rstrip(".") or "New tool")
        plan = {"title": title, "summary": f"A one-page tool for: {job[:120]}", "icon": "list", "section": "My tools", "roles": ["owner", "admin", "manager", "user"],
                "data": [{"name": "items", "purpose": "One row per item.", "columns": ["name:text", "owner:text", "due:date", "status:text"]}],
                "pages": [{"name": "main", "title": title, "purpose": "The list and a form to add to it.", "blocks": "the table and a form beside it"}],
                "flows": [], "code": [], "team": [], "left_out": ""}
    if change:
        plan["summary"] += f" Changed as you asked: {change[:120]}"
    return plan


def _ceo_revise(d: dict) -> dict:
    pages = list(((d.get("draft") or {}).get("pages") or {}).keys())
    return {"changes": [{"kind": "page", "name": pages[0] if pages else "main", "instruction": d.get("owner_says", "")[:200]}]}


def _ceo_summary(d: dict) -> dict:
    tasks = d.get("tasks") or []
    probs = [t for t in tasks if t.get("status") == "problem"]
    dr = d.get("draft") or {}
    return {"summary": f"The team built {d.get('tool')} as a Builder draft ({d.get('slug')}): {len(dr.get('tables') or {})} tables, {len(dr.get('pages') or {})} pages, {len(dr.get('flows') or {})} flows. "
                       + (f"{len(probs)} task(s) need you." if probs else "Every check passed.") + " The flows are switched off.",
            "next": ["Open it in the Builder and look at each page in the preview.", "Switch the flows on when you are happy with them.", "Release it from the Builder."]}


def _team_data(d: dict) -> dict:
    if d.get("current"):
        cur = d["current"]
        return {"columns": cur.get("columns") or [], "write": cur.get("write", "everyone"), "scope": cur.get("scope", "all")}
    t = d.get("table") or {}
    cols = []
    for c in t.get("columns") or ["name:text"]:
        key, _, ty = str(c).partition(":")
        m = re.match(r"^(choice|link)\((.*)\)$", ty)
        extra = {}
        if m and m.group(1) == "choice":
            ty, extra = "choice", {"options": [o.strip() for o in m.group(2).split("|") if o.strip()]}
        elif m:
            ty, extra = "link", {"table": m.group(2).strip()}
        cols.append({"key": key, "type": ty or "text", "label": key.replace("_", " ").capitalize(), **extra, **({"required": True} if key in ("name", "task") else {})})
    return {"columns": cols, "write": "everyone", "scope": "all"}


def _team_flow(d: dict) -> dict:
    f = d.get("flow") or {}
    tables = d.get("tables") or {}
    tname = "tasks" if "tasks" in tables else next(iter(tables), "")
    cols = [c.split(":")[0] for c in tables.get(tname, [])]
    due = next((c for c in cols if "due" in c), None)
    where = [{"field": due, "op": "past", "value": ""}] if due else []
    if f.get("trigger") == "schedule":
        return {"title": f.get("title") or "Morning check", "trigger": {"type": "schedule", "every": "weekday", "at": "07:00"}, "run_as": "owner", "start": "find",
                "steps": {"find": {"type": "find_rows", "data": tname, "where": where, "as": "late", "next": "tell"},
                          "tell": {"type": "notify", "who": {"kind": "owner"}, "title": "Late tasks", "text": "{{late_count}} task(s) are past their due date."}}}
    who = next((c for c in cols if "email" in c), None)
    each_body = {"type": "email", "to": "{{item.%s}}" % who, "subject": "A task is late", "text": "Hello — “{{item.task}}” was due {{item.%s}}." % (due or "due")} if who else {"type": "notify", "who": {"kind": "starter"}, "title": "Late", "text": "{{item.task}} is late."}
    return {"title": f.get("title") or "Remind late tasks", "trigger": {"type": "button"}, "run_as": "starter", "start": "find",
            "steps": {"find": {"type": "find_rows", "data": tname, "where": where, "as": "items", "next": "each"},
                      "each": {"type": "for_each", "items": "items", "as": "item", "body": "send", "next": "done"},
                      "send": each_body,
                      "done": {"type": "notify", "who": {"kind": "starter"}, "title": "Reminders sent", "text": "{{items_count}} reminder(s)."}}}


def _team_page(d: dict) -> dict:
    p = d.get("page") or {}
    name = p.get("name", "main")
    dr = d.get("draft") or {}
    tables = dr.get("tables") or {}
    table = name if name in tables else ("tasks" if "tasks" in tables and "task" in name else next(iter(tables), ""))
    cols = [c.split(":")[0] for c in tables.get(table, [])]
    filt = [c.split(":")[0] for c in tables.get(table, []) if ":choice" in c or ":link" in c][:2]
    left = [{"id": f"{name}_list", "type": "table", "title": p.get("title") or "List", "source": {"data": table}, "columns": [{"key": c} for c in cols[:5]], "search": True, **({"filters": filt} if filt else {})}] if table else []
    right = [{"id": f"{name}_add", "type": "form", "title": "Add one", "source": {"data": table}, "fields": [{"key": c} for c in cols[:5]], "submit": "Add"}] if table else []
    btn = [f for f in (d.get("button_flows") or [])]
    if btn and name != "projects":
        right.append({"id": f"{name}_run", "type": "button", "flow": btn[0], "label": "Send the reminders", "confirm": "Send the reminders now?"})
    top = []
    if name == "projects":
        top = [{"id": f"{name}_counts", "type": "metrics", "items": [{"metric": "flows.waiting_for_me"}, {"metric": "flows.runs_today"}]}]
    secs = ([{"columns": [{"width": 12, "blocks": top}]}] if top else []) + [{"columns": [{"width": 8, "blocks": left}, {"width": 4, "blocks": right}]}]
    return {"title": p.get("title") or name.capitalize(), "intro": p.get("purpose") or "", "width": "wide", "sections": secs}


def _team_fix(d: dict) -> dict:
    cur = d.get("current_page") or {}
    if cur.get("blocks"):
        # keep every block by its id, and add the change as a line of text
        return {"title": cur.get("title") or "Page", "intro": "", "width": "wide", "sections": [{"columns": [{"width": 12, "blocks": [{"id": b["id"]} for b in cur["blocks"]]
                + [{"id": "change_note", "type": "text", "text": (d.get("instruction") or "Changed as asked.")[:120]}]}]}]}
    return _team_page(d)


def _team_look(d: dict) -> dict:
    page = d.get("page") or {}
    n = len(page.get("blocks") or [])
    return {"score": 86 if n <= 6 else 70, "verdict": "Reads like the rest of CBX-Connect: numbers, then the list, the form beside it." if n <= 6 else "Too many blocks for one page.",
            "good": ["One job per page", "Form beside the table"], "problems": [] if n <= 6 else ["More than six blocks"], "fixes": [] if n <= 6 else [{"block": "", "change": "Move the form to its own page"}]}


def _team_words(d: dict) -> dict:
    # the stub cannot translate: it leaves the words out, so English shows and the tester counts the gap
    return {"words": {}}


def _team_readme(d: dict) -> dict:
    dr = d.get("draft") or {}
    lines = [f"# {d.get('tool')}", "", d.get("summary") or "", "", "## Pages", ""] + [f"- **{v.get('title')}** (`pages/{k}.json`)" for k, v in (dr.get("pages") or {}).items()]
    lines += ["", "## Tables", ""] + [f"- `{k}`: {', '.join(v)}" for k, v in (dr.get("tables") or {}).items()]
    lines += ["", "## Flows", ""] + [f"- **{v.get('title')}** — {v.get('trigger')}; ships switched off" for k, v in (dr.get("flows") or {}).items()]
    lines += ["", "## Owner", "", str(d.get("owner") or ""), "", "## Changing it", "", "Admin → Builder → this tool. Save the draft, preview it, release it."]
    return {"readme": "\n".join(lines)}


def _team_sample(d: dict) -> dict:
    return {"rows": {}}  # the engine makes believable rows itself when the model gives none
