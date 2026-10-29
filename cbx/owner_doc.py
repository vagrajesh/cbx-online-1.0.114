"""The owner's document (1.0.58): compiled, not hand-written.

Where is what, who owns what, how to change it, release it, roll it back and restore it — put together from
what the server actually holds: the tool folders (manifests, pages, tables, rules, READMEs), the settings in
force (names, and values only where a value is not a secret), the roles, the releases and the backups.

`compile(db)` is the live one (Admin → Versions → The owner's document; owner only). `compile(None)` leaves
out what only a running server knows (people, releases, backups); that copy is sealed into every release
zip as docs/OWNERS-DOCUMENT.md. English only (decision 6). Never a password, key, token, account number or a
person's data beyond names and roles.
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

# settings shown with their value (nothing secret); every other setting is only named as set / not set
SHOWN = ("host", "port", "allowed_hosts", "timezone", "model_mode", "cexa_model", "mfa_required", "mfa_email_fallback", "mfa_admin_trust_days", "mfa_max_failed",
         "code_fallback", "device_trust_days", "login_max_attempts", "login_lockout_minutes", "max_attachment_mb", "mail_enabled", "flows_schedule", "teams_auto",
         "backups", "backup_at", "backup_keep_days")
SECRETISH = re.compile(r"(pass|secret|token|key|database_url|client_id|tenant)", re.I)

CORE_SCREENS = [
    ("Home", "Everyone", "A dashboard per role; each person arranges their own (Customise)."),
    ("Help", "Everyone", "Lessons and how-tos."),
    ("Requests", "Everyone", "Ask for something; follow what was asked; results."),
    ("Messages", "Everyone", "Chat with people and with C-EXA."),
    ("Approvals", "Everyone", "What a rule is waiting for you to approve; every run of every rule."),
    ("Work", "Owner, admins", "The request queue and the build tickets."),
    ("Builder", "Owner, admins", "Make and change tool folders: pages, tables, rules, words; release them."),
    ("People & access", "Owner, admins (managers: their team)", "People, roles, teams, grants, two-step, View as."),
    ("Audit trail", "Owner, admins", "Everything anyone (and C-EXA) did, append-only."),
    ("Versions", "Owner, admins", "Upload a release, test it, promote it with the ten-minute notice, roll it back; this document."),
    ("C-EXA", "Owner (the board: read-only)", "Talk to C-EXA, her teams and their jobs, C-EXA as developer."),
    ("Settings", "Everyone (their own); the owner the rest", "Appearance, language, security; mail, API connections, warehouse, branding."),
    ("PDF Studio", "People with the PDF Studio grant", "Read batches of PDFs with templates and learnt profiles; answer its questions; Harvest; master sheets."),
    ("Entity Compliance screens", "People with the grant", "Behind the Entity Compliance folder: file and renew documents, companies, reminders."),
]

HOW_TO = [
    ("Change a tool", [
        "Admin → Builder → choose the tool at the top.",
        "Click a part on the page to change it, or use the outline on the left (Pages, Data, Rules, The tool).",
        "Save draft (Ctrl+S). The foot of the outline says what to fix before a release.",
        "Words in 4 languages: fill the other three, or press Let C-EXA translate the missing words and read them.",
        "Release… → it becomes a version on the Versions page (below)."]),
    ("Release a version", [
        "Admin → Versions → Upload & test (a cbx-online-<version>.zip, or a Builder release).",
        "The tests run in a sandbox; the council (C-EXA's Governance team) reviews the change.",
        "Test in sandbox opens it in a copy of the app if you want to look first.",
        "Promote → everyone signed in gets a ten-minute notice, then the server restarts on the new version. Your .env is never overwritten.",
        # 1.0.114
        "Only upload versions you trust. Their tests run on this server under the server's own Windows account: since 1.0.114 they no longer see the server's settings and secrets in the environment, but that account can still read the .env file, data\\secret.key and the live database from disk. The real fix is an IT step: run the tests under a separate Windows account with low rights that cannot read the CBX data folder or .env."]),
    ("Roll back", [
        "Admin → Versions → the version that is live → Roll back. The live files are put back from the backup taken when it was promoted. From 1.0.114 the database is backed up beside them at every promote, and Roll back offers to put the data back too (off unless you tick it). Everything saved since the promote is replaced: people added since are gone and access given since is undone. Security changes made since are kept (disabled, locked or removed access stays so; passwords and two-step settings stay current), the audit trail since is copied back, and everyone is signed out. What it replaces is kept beside the backup.",
        "Only the version before the live one can be rolled back to this way; for older ones restore a backup (below)."]),
    ("Restore a backup (e.g. into a test copy)", [
        "Backups are made every night (see Backups below) into the backup folder; each night is a folder named by its date and time.",
        "Into a test copy: make a new folder, copy the snapshot into it (cbx.sqlite3, tools\\, secret.key, branding\\, builder\\), copy files\\warehouse to warehouse\\ and files\\tool-files to tools\\_files\\, then start a second CBX with CBX_DATA_DIR set to that folder and another CBX_PORT.",
        "Onto the live server: stop CBX (close run.bat), keep a copy of the data folder as it is, copy the snapshot over it the same way, start run.bat. People lose what they did after the snapshot."]),
    ("Unlock the owner", ["On the server, run unlock-owner.bat (in the CBX-Online folder, beside the app): it clears a locked owner account."]),
    ("Unlock someone, or reset their two-step", ["People & access → the person → Edit → tick Unlock; or Reset two-step (a reason is asked and kept)."]),
    ("See what someone sees", ["People & access → View as (read-only, up to an hour, written to the audit trail)."]),
    ("Switch a rule on or off", ["Builder → the tool → Rules → the rule → On / Off → Save draft → release."]),
    ("Let another system read a table", [
        "Builder → the tool → the table → Other systems may read it: tick the columns (never files) → Save draft → release.",
        "Settings → API connections → Keys for our data → Make a key: who it is for, the table, how long (90 days unless you say; a year at most), calls a minute, and the addresses it may come from.",
        "Copy the key when it is shown — it is shown once. The other system reads GET /api/shared/<tool>/<table> with Authorization: Bearer <the key>, over HTTPS.",
        "Lost or leaving: Revoke. It stops at once. Every key made, refused or revoked is in the audit trail."]),
]

TEN_THINGS = [
    ("Sign in as owner and unlock a locked account.", "How to → Unlock"),
    ("Create a user, give them one company's folder only, then take it away.", "People & access"),
    ("Start dev on your box and open one tool on its own.", "How to → Restore a backup (into a test copy)"),
    ("Build the unregistered sign-ins tool in the Builder and release it.", "How to → Change a tool, Release"),
    ("Change a menu label in all four languages and release it.", "How to → Change a tool"),
    ("Roll back a release.", "How to → Roll back"),
    ("Restore last night's backup into dev.", "How to → Restore a backup; Backups"),
    ("Open any tool folder and say what each of its seven things does.", "The tools"),
    ("Ask C-EXA Developer for a change, review it, and reject it.", "C-EXA → C-EXA as developer"),
    ("Answer IT's questions from the owner's document alone.", "this document"),
]


def _cell(v) -> str:
    return str(v if v is not None else "").replace("|", "/").replace("\n", " ").strip()


def _table(head: list, rows: list) -> str:
    if not rows:
        return "_None._\n"
    out = ["| " + " | ".join(head) + " |", "| " + " | ".join("---" for _ in head) + " |"]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def _settings_rows() -> list:
    from .config import settings

    rows = []
    for name, field in type(settings).model_fields.items():
        v = getattr(settings, name, None)
        if name in SHOWN:
            rows.append((f"CBX_{name.upper()}", v if v not in ("", None) else "(empty)"))
        elif SECRETISH.search(name):
            rows.append((f"CBX_{name.upper()}", "set" if v not in ("", None, 0, False) else "not set"))
    return rows


def _tools_section(tools_root: Optional[Path] = None) -> list[str]:
    from .engine import actions as A
    from .engine import loader

    A.load()
    reg = loader.load_all(tools_root) if tools_root else loader.registry()
    out = ["## The tools", "", "Each tool is a folder under `tools\\` (manifest, pages, data, rules, words in four languages, tests, README). The Builder edits a copy; a release puts it live.", ""]
    rows = []
    for t in sorted(list(reg.tools.values()) + list(reg.broken.values()), key=lambda x: x.slug):
        m = t.manifest or {}
        acc = m.get("access") or {}
        who = "everyone" if acc.get("everyone") else ", ".join(filter(None, [", ".join(acc.get("roles") or []), f"grant {acc['tool']}" if acc.get("tool") else ""]))
        rows.append((t.text(m.get("title", "tool.title")) or t.slug, t.slug, m.get("owner", ""), who or "—", len(t.pages), len(t.data), len(t.flows), "sound" if t.slug in reg.tools else "BROKEN"))
    out.append(_table(["Tool", "Folder", "Owner", "Who uses it", "Pages", "Tables", "Rules", "State"], rows))
    for t in sorted(reg.tools.values(), key=lambda x: x.slug):
        m = t.manifest or {}
        out += ["", f"### {t.text(m.get('title', 'tool.title')) or t.slug} (`tools\\{t.slug}`)", ""]
        if t.data:
            out += ["**Tables** (rows in `data\\tools\\" + t.slug + ".sqlite`)", ""]
            out.append(_table(["Table", "Columns", "Who sees the rows", "Who adds"], [(n, ", ".join(c.get("key", "") for c in (b.get("columns") or [])), b.get("scope", "all") + (f" (+ team where {b['share']['field']} is {b['share']['value']})" if isinstance(b.get("share"), dict) else ""), b.get("write", "everyone")) for n, b in sorted(t.data.items())]))
        if t.flows:
            out += ["", "**Rules**", ""]
            rr = []
            for n, f in sorted(t.flows.items()):
                tr = f.get("trigger") or {}
                jobs = sorted({(A.ACTIONS[s["action"]].title if s.get("action") in A.ACTIONS else s.get("action")) for s in (f.get("steps") or {}).values() if isinstance(s, dict) and s.get("type") == "action"})
                rr.append((t.text(f.get("title")) or n, tr.get("type", "") + (f" on {tr['data']}" if tr.get("data") else ""), "on" if f.get("enabled", True) else "off", f.get("run_as", "starter"), ", ".join(jobs) or "—"))
            out.append(_table(["Rule", "When", "On/off", "Runs as", "Server jobs"], rr))
        readme = (t.path / "README.md").read_text(encoding="utf-8") if (t.path / "README.md").is_file() else ""
        if readme.strip():
            body = re.sub(r"^#\s.*\n", "", readme, count=1)
            body = re.sub(r"^(#{1,4})\s", lambda mm: "#" * min(6, len(mm.group(1)) + 3) + " ", body, flags=re.M)
            out += ["", "**Its README**", "", body.strip(), ""]
    jobs = A.catalogue()
    out += ["", "### Server jobs the rules may run", "", _table(["Job", "Group", "Background", "What it does"], [(j["title"], j["group"], "yes" if j["slow"] else "no", j["description"]) for j in jobs])]
    return out


def _open_section(db, tools_root: Optional[Path] = None) -> list[str]:
    """What other systems may read (1.0.59): the tables the folders open, and (on the live server) the keys.
    Never a key — who it is for, what it reads, its limits and its state."""
    from .api.shares_api import describe, exposable
    from .engine import loader

    tables = exposable(loader.load_all(tools_root) if tools_root else None)
    out = ["", "## Open to other systems", "",
           "Other systems read a table only with a key the owner makes (Settings → API connections → Keys for our data): the columns listed here, reading only, never files, over HTTPS.", ""]
    out += [_table(["Table", "Columns they may read", "Who sees its rows here"], [(f"{t['tool_name']} · {t['table']}", ", ".join(c["label"] for c in t["columns"]), t["scope"]) for t in tables])] if tables else ["No table is open to other systems."]
    if db is not None:
        from .models import DataKey

        ks = [describe(k, db) for k in db.query(DataKey).order_by(DataKey.id.desc()).limit(50).all()]
        out += ["", _table(["Key for", "Reads", "Calls a minute", "From", "Until", "State", "Calls"],
                           [(k["name"], f"{k['tool']} · {k['table']}", k["rate_per_min"], ", ".join(k["allowed_ips"]) or "anywhere", k["expires_at"][:10], k["state"], k["calls"]) for k in ks])] if ks else ["", "No keys have been made."]
    return out


def compile(db=None, tools_root: Optional[Path] = None) -> str:  # noqa: A001 — the word the document uses
    from . import VERSION
    from .branding import ai_name, product_name
    from .config import ROOT, settings

    from .engine.flows import local_now

    now = local_now().strftime("%d %B %Y, %H:%M")
    live = db is not None
    L = [f"# {product_name()} — the owner's document", "", f"Version {VERSION} · compiled {now}" + ("" if live else " for the release zip (people, releases and backups are on the live page)") + ".", "",
         "Compiled by the server from what it holds; regenerated every release. Owner only. It never holds a password, key or token.", ""]
    L += ["## Where is what", ""]
    from . import backup as BK

    db_kind = "SQLite" if settings.db_url.startswith("sqlite") else "Postgres"
    L.append(_table(["What", "Where"], [
        ("The app (code)", str(ROOT) if live else "the folder you unzipped (next to run.bat)"),
        ("The data folder", str(settings.data_dir) if live else "data\\ in the app folder, or CBX_DATA_DIR"),
        ("The main database", f"{db_kind}: data\\cbx.sqlite3" if db_kind == "SQLite" else "Postgres (CBX_DATABASE_URL)"),
        ("Each tool's tables", "data\\tools\\<tool>.sqlite; files on rows in data\\tools\\_files\\"),
        ("Uploaded documents (the warehouse)", "data\\warehouse\\"),
        ("The key that seals stored secrets", "data\\secret.key — back it up with the data; without it sealed secrets cannot be read"),
        ("Backups of the data", (str(BK.root()) if live else "CBX_BACKUP_DIR, default data\\backups\\data") + f"; every night at {settings.backup_at}, {settings.backup_keep_days} days kept"),
        ("Backups of the app at each promote", "data\\backups\\<version>-<time>\\ (what Roll back uses; the database copy is in its database\\ folder)"),
        ("Settings", ".env next to run.bat (never overwritten by a release); branding and API connections in the app"),
        ("Tool folders", "tools\\ in the app folder; Builder drafts in data\\builder\\tools\\"),
    ]))
    if live:
        L += ["", "### Settings in force", "", "Values only where nothing is secret; secrets are only said to be set.", "", _table(["Setting", "Value"], _settings_rows())]
    L += ["", "## Built-in screens", "", _table(["Screen", "Who", "What it is for"], CORE_SCREENS)]
    L += _tools_section(tools_root)
    if live:
        from .models import AppVersion, Role, User

        roles = db.query(Role).order_by(Role.rank.desc()).all()
        counts = {}
        for u in db.query(User).filter(User.is_active.is_(True), User.is_axl.is_(False)).all():
            counts[u.role] = counts.get(u.role, 0) + 1
        owners = [u.name for u in db.query(User).filter(User.role == "owner", User.is_active.is_(True)).all()]
        L += ["", "## Who owns what", "", f"**Owner:** {', '.join(owners) or '—'}. **The AI:** {ai_name()} (a service account; it never grants access or promotes).", "",
              _table(["Role", "People", "What it may do", "Built in"], [(r.name, counts.get(r.slug, 0), f"{len(r.caps or [])} permissions" + (f" — {r.description}" if r.description else ""), "yes" if r.locked else "no") for r in roles])]
        vs = db.query(AppVersion).order_by(AppVersion.created_at.desc()).limit(15).all()
        L += ["", "## Releases", "", _table(["Version", "Status", "Uploaded", "Promoted"], [(v.tag, v.status, v.created_at.strftime("%d %b %Y") if v.created_at else "", v.promoted_at.strftime("%d %b %Y %H:%M") if v.promoted_at else "") for v in vs])]
        snaps = BK.snapshots()[:7]
        L += ["", "## Backups", "", f"Folder: `{BK.root()}` · every night at {settings.backup_at} ({'on' if settings.backups else 'OFF'}) · {settings.backup_keep_days} days kept.", "",
              _table(["Snapshot", "Databases", "Size", "Files mirrored", "State"], [(s["name"], s["databases"], f"{s['bytes'] / 1048576:.1f} MB", s["files_copied"], "ok" if s["ok"] else ("FAILED: " + s["note"])) for s in snaps])]
    L += _open_section(db if live else None, tools_root)
    L += ["", "## How to", ""]
    for title, steps in HOW_TO:
        L += [f"### {title}", ""] + [f"{i}. {s}" for i, s in enumerate(steps, 1)] + [""]
    L += ["## The ten things (the acceptance)", "", _table(["", "Where it is in this document"], TEN_THINGS)]
    return "\n".join(L).rstrip() + "\n"


# ---- a small markdown → HTML for this document (headings, tables, lists, code, bold) ---------------------
def to_html(md: str, title: str = "The owner's document") -> str:
    def inline(s: str) -> str:
        s = html.escape(s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        return re.sub(r"(?<![\w*])_(.+?)_(?![\w*])", r"<i>\1</i>", s)

    out, lines, i = [], md.split("\n"), 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append("<pre>" + html.escape("\n".join(lines[i + 1:j])) + "</pre>")
            i = j + 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            n = len(m.group(1))
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
            i += 1
            continue
        if ln.startswith("| "):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, body = rows[0], [r for r in rows[2:]]
            out.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table>")
            continue
        if re.match(r"^(\d+\.|-)\s", ln):
            tag = "ol" if ln[0].isdigit() else "ul"
            items = []
            while i < len(lines) and re.match(r"^(\d+\.|-)\s", lines[i]):
                items.append(re.sub(r"^(\d+\.|-)\s+", "", lines[i]))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        if ln.strip():
            para = [ln]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,6}\s|\| |```|(\d+\.|-)\s)", lines[i]):
                para.append(lines[i])
                i += 1
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            continue
        i += 1
    css = "body{font:14px/1.55 Inter,system-ui,sans-serif;color:#21262c;max-width:1000px;margin:32px auto;padding:0 20px}h1{font-size:24px}h2{margin-top:32px;border-bottom:1px solid #ddd;padding-bottom:4px}table{border-collapse:collapse;width:100%;margin:8px 0}th,td{border:1px solid #ddd;padding:5px 8px;text-align:left;vertical-align:top}th{background:#f4f5f6}code{background:#f4f5f6;padding:1px 4px;border-radius:3px}pre{background:#f4f5f6;padding:10px;overflow:auto}@media print{body{margin:0}}"
    return f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>{html.escape(title)}</title><style>{css}</style></head><body>{''.join(out)}</body></html>"


def body_html(md: str) -> str:
    """Only the document's own HTML (for showing inside the app)."""
    full = to_html(md)
    return full[full.index("<body>") + 6: full.rindex("</body>")]


def sealed(src: Path) -> str:
    """The copy that goes into a release zip (no people, releases or backups), from that release's own tools."""
    try:
        return compile(None, tools_root=src / "tools" if (src / "tools").is_dir() else None)
    except Exception as e:  # noqa: BLE001 — a release is never refused over its document
        return f"# The owner's document\n\nCould not be compiled for this zip ({type(e).__name__}: {str(e)[:200]}). Open it on the live server: Admin → Versions → The owner's document.\n"
