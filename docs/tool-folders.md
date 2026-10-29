# Tool folders and the Builder — the owner's page

This is the page to read before touching a tool. It says what a tool folder is, what each file in it
does, how the Builder turns into that folder, how a change gets from your screen to live, and
how to build your first tool without writing code.

## 0. The Builder, in one picture (1.0.53)

```
 the tool (outline)          the page as people see it                    the selected part, as questions
 ┌──────────────────┐   ┌─────────────────────────────────────┐   ┌──────────────────────────────────┐
 │ PAGES            │   │ Utility bills · Bills               │   │ TABLE                            │
 │  Bills · first   │   │ ┌──────────────────────────────┐    │   │ Show every row of Municipal bills│
 │   Unpaid bills   │   │ │ Unpaid bills          [table]│◀───┼───│ where Status is Unpaid, by Due.  │
 │   Chart          │   │ └──────────────────────────────┘    │   │ 1 Take the data from  Warehouse  │
 │ DATA             │   │          + add here                 │   │ 2 Which data  Municipal bills    │
 │ RULES            │   │ ┌──────────────────────────────┐    │   │ 3 Show it as  Every row          │
 │  ● Remind …      │   │ │ ▇▇▇▇▇▇ Ekurhuleni            │    │   │ 4 Columns  Site  Amount  Due …   │
 │ THE TOOL         │   │ └──────────────────────────────┘    │   │ 5 Only rows where Status is …    │
 └──────────────────┘   └─────────────────────────────────────┘   └──────────────────────────────────┘
```

One screen, no tabs. **Left:** the tool as an outline — its pages (and the parts of the page you are
on), its data (tables of its own), its rules (when this happens, then that), and the tool itself (name,
menu, who uses it, the words in four languages, About, Release). At the bottom: what to fix before a
release, in plain sentences. **Middle:** the page with live rows. Click a part to change it, or
**+ add here** between parts to add one (Show · Collect · Do · Arrange · Advanced). *Try it* lets you
search and sort as people would; the language list shows it in Arabic, Portuguese or French.
**Right:** whatever is selected, as numbered questions. Nothing asks for a key or a line of code: the
words you type become the tool's English and the Builder files them under keys.

**Rules** are *WHEN* (a button, a new row, a changed row, a schedule) · *ROWS* (optional: go through
the rows of a table, a warehouse sheet or another tool's data, only those where …) · *IF* (only if …)
· *THEN* (tell someone, email, ask for approval, change the row, add a row, wait, call an API, stop) ·
*AFTER* (once, at the end). In a message, `{Site}` puts in that row's Site; the chips under the rule
put them in for you. **Try it** runs the rule with the live rows and writes, sends and calls nothing.
A rule is a flow file (`flows/<name>.json`) underneath; a flow with a shape the plain form cannot show
(an If with a "no" branch) opens read-only and keeps running as it is.

## 1. A tool is a folder

```
tools/
  conftest.py                 lets each folder's tests use the suite's fixtures — leave it
  entity-compliance/          one folder per tool
    tool.json                 the manifest: name key, icon, menu place, who may use it, home page
    pages/grid.json           a Page block: type grid, drawn from the provider compliance.grid
    pages/documents.json      a Page block: type table, drawn from the provider compliance.documents
    data/                     Data blocks: the tool's own tables (this tool has none — it reads providers)
    flows/                    Flows: when this happens, do these steps (1.0.48) — this tool ships one, switched off
    languages/en.json         every word the tool shows, English
    languages/ar.json         the same keys, Arabic (drawn right to left)
    languages/pt.json         Portuguese
    languages/fr.json         French
    tests/test_entity_compliance.py   the folder's own tests, run with the suite
    README.md                 this tool's page in your document: what, who, where, how to change
```

Every folder has the same seven things. Once you know one, you know them all.

### tool.json

| Key | Meaning |
| --- | --- |
| `slug` | the folder name; also the address `#/tool?t=<slug>` |
| `title` | a **key** into `languages/` (never the words themselves) — `tool.title` |
| `icon` | one of the icons the Builder lists |
| `menu.section` | `My tools`, `Workspace` or `Admin` (Admin only for owner and admin roles) |
| `menu.order` | where it sits in its section (lower = higher) |
| `access.roles` | roles that always see it, e.g. `["owner", "admin"]` |
| `access.tool` | a grant from Users & roles that also lets people in, e.g. `doc-watch` |
| `home` | the page that opens from the menu |
| `badge` | a small word beside the menu name — `new` while a folder runs beside the page it replaces |
| `access.everyone` | `true`: every role may use it (Home) |
| `menu.hidden` | `true`: not in the menu (Home is reached as Home, not from a menu row) |
| `owner` | the accountable person's email |

### A page (`pages/<name>.json`)

| Key | Meaning |
| --- | --- |
| `title`, `intro` | keys into `languages/` |
| `menu` | shown in the tool's tabs |
| `style.width` | `narrow`, `normal` or `wide` |
| `layout.sections[].columns[]` | the columns (widths add up to 12) and, in each, the **blocks** in order |
| `canvas.widgets` | where each block sits on the Builder's canvas (the picture only) |

A page written the 1.0.43 way (`type` + `source` at the top) still works: it is one full-width block.

### The blocks (`layout … widgets[]`)

Every block has an `id`, a `type` and, optionally, a `title` key. Then, by type:

| Block | Its own keys | Needs a source |
| --- | --- | --- |
| `button` | `flow` (one of this tool's flows started by a button), `label` (key), `confirm` (key, optional), `primary` | |
| `metrics` | `items[].metric` — numbers from Core's catalogue (`cbx/engine/metrics.py`), each a tile; optional `label` key | |
| `heading` | `text` (key), `level` 1–3 | |
| `text` | `text` (key; **bold**, *italic*, `- lists`), `tone` | |
| `stats` | `items[].key` — the summary numbers to show as tiles | grid provider |
| `table` | `columns`, `filters`, `params` (fixed filter values), `search`, `row_link`, `limit` | provider, data or api |
| `grid` | — | grid provider |
| `form` | `fields`, `submit` (key) | data |
| `image` | `asset` (a file in `assets/`), `alt`, `caption`, `width`, `align` | |
| `embed` | `url` (https), `height`, `note` | |
| `code` | `code` (HTML, CSS, JavaScript — runs in its own sandboxed frame), `height` | |
| `links` | `kind` (`links` or `buttons`), `items[]` with `label` (key) and `url` or `route`; `align: "end"` puts buttons on the far side | a `url` under `/api/` (1.0.99) is a download CBX serves, e.g. `/api/ec/export` |
| `divider`, `spacer` | `height` (spacer) | |

**Sources.** `source.provider` (Core's data — `compliance.grid`, `compliance.documents`,
`core.sign_in_attempts`), `source.data` (one of the tool's own Data blocks) or `source.api` (an API
connection from Admin → API connections, with `path`, `rows` — where the list is in the answer, e.g.
`data.items` — and `columns[]` with `key`, `from` (a path inside each row) and `type`). The connection's
key never leaves the server, and an API is asked at most once every 30 seconds per block, however
many screens show it.

**A code block** is served as its own small page inside a sandboxed frame: it can run its scripts and
load from the web, but it has no access to CBX's cookies, pages or data as the signed-in person. It is
where a snippet from Claude, your model or a developer goes. **An embedded website** is framed as it is;
some sites refuse to be framed (Google does) — then the link under the frame opens it in a tab.

### A flow (`flows/<name>.json`)

"When this happens, do these steps." Made on the Builder's **Flows** tab; the file is what the canvas
compiles to:

| Key | Meaning |
| --- | --- |
| `title` | a language key — `flow.<name>.title` |
| `enabled` | `false` switches it off (it still tests) |
| `trigger` | `{type: button}` (a Run button block starts it; `roles` limits who), `{type: row_added / row_changed, data: <table>}`, or `{type: schedule, every: hour / day / weekday / week / month, at: "07:00", days / day / minute}` — the Job |
| `run_as` | `starter` (the person who started it) or `owner` (the tool's owner; a schedule always) |
| `start` | the first step's id |
| `steps` | `{id: {type, …, next}}`; `if` and `approval` have `yes` / `no`, `for_each` has `body` (each) and `next` (then) |
| `canvas` | positions only |

Steps: `find_rows` (`provider` or `data`, `params`, `where` [{field, op, value}], `as`, `limit`) ·
`if` (`when` [{left, op, value}], `match` all / any) · `for_each` (`items`, `as`) · `add_row` /
`update_row` (`data`, `values`, `row` for the id) · `set` (`name`, `value`) · `notify` and `approval`
(`who`: {kind: starter / owner / roles / people, roles, emails}; `title`/`text`, or `question`) ·
`email` (`to`, `subject`, `text`, `must_send`) · `wait` (`minutes`, `hours`, `days`) · `api`
(`connection`, `method`, `path`, `payload`, `as`) · `stop` (`outcome` done / failed, `message`).
Comparisons: equals, not_equals, gt, gte, lt, lte, contains, not_contains, empty, not_empty,
days_from_today, within_days, past. Values are `{{templates}}`: `{{today}}`, `{{person.name}}`,
`{{trigger.row.<column>}}`, `{{<as>.<column>}}`, `{{<as>_count}}`, `{{approval.note}}`.

The runner is `cbx/engine/flows.py`; runs are `FlowRun` rows (Approvals → All runs). A flow's message
and email texts are content, written in the language you write them in — they are not language keys.

### A Data block (`data/<name>.json`)

`kind: table`, `columns` (each `key`, `type` — text, int, number, date, bool, json — `label`, `required`),
`write` (`everyone`, `admins`, `nobody`) and `scope` (`all` or `mine`). The table lives in
`data\tools\<slug>.sqlite`, one file per tool. Every table also has `id`, `created_at`, `created_by`.
A column removed from the block stays in the file, ignored; nothing is ever dropped by a definition.

Three column types since 1.0.51:

| Type | Settings | What people get |
| --- | --- | --- |
| `choice` | `options`: 2 to 50 values, e.g. `["Open", "In progress", "Done"]` | a pick-list on forms; the table shows it as a pill; a table can filter by it. Anything else is refused. The words are `opt.<table>.<column>.<value>` (optional — the value shows without them) |
| `link` | `table`: another table of the tool; `show`: which of its columns to show (default: its first text column) | a pick-list of that table's rows; the table shows the row's label, not its number; a table can filter by it. A row others point at cannot be deleted until they are changed |
| `file` | `accept`: pdf, png, jpg, jpeg, gif, webp, txt, csv, docx, xlsx, pptx, doc, xls, msg, eml, zip (default: all of them); `max_mb`: 1 to 25 (default 10) | one file per row, uploaded once the row is saved. Kept in `data\tools\_files\<slug>\`, under a random name; checked by its first bytes, not just its name; always downloaded, never opened in the page. Deleting the row deletes the file. A flow cannot put a file on a row |

1.0.54 adds `person` (`who`: `everyone` or `team`; shows the name; `{{person.id}}` in a condition is
the person looking), `checklist` (a list of steps with ticks) and `calc` (`calc`: `{"fn": "count"|"done",
"of": <checklist>}`, `{"fn": "days", "from": <date>|"created", "to": <date>|"today"}`, or `{"fn": "grade",
"of": <number>, "grades": [[3, "Small"], [7, "Medium"], [null, "Complex"]]}` — worked out on every save
and read). A table's `scope` is `all`, `mine` (added by or named in; a manager also their team's) or
`team`. A Board block (`type: board`, `lane`: a choice column, `card`, `fields`) and a table's
`actions` (`[{label, flow}]`: a button on each row; the rule's trigger is `{"type": "button", "data":
<table>}` and it gets the row as `{{trigger.row}}`).

1.0.55 adds, for any tool (To-do is made of them — `tools/todo` is the worked example):

| Key | Where | Meaning |
| --- | --- | --- |
| `write: "managers"` | table | managers and admins add rows |
| `scope: "members"`, `members: {table, person, link, through, only}` | table | only the people in another table: e.g. `jobs` rows for the active people of `job_members` (`person` its person column, `link` its link to the job, `through` `id` — this row — or this table's own link column, `only` e.g. `{"status": "Active"}`); and whoever added the row |
| `share: {field, value}` | table with `scope: "mine"` | the whole team also sees the rows where that pick-list has that value |
| `unique: [["goal", "person"]]` | table | those columns together only once |
| `default` | column | a choice's option, `today` (date) or `me` (person); a form shows it filled in, and a row that does not say gets it |
| `preset: {column: value}` | table, board, form | what a row added from that block starts with |
| `type: "progress"` | block | `source.data` (the goals), `label`, `target` (a number column), `count: {data, where}` (the rows that count; a value may be `{{row.<goal column>}}`, and `skip_empty` makes an empty goal value mean any), `fields`, `actions` |
| `actions[].ask: [{key, type: date|text, label}]`, `actions[].reached` | table, progress | ask first (the answer is `{{trigger.values.<key>}}`); only once the goal is reached — checked on the server |

Rules: an approval's `no` holds the steps for *declined*; `who` may be `{"kind": "person", "person":
"{{trigger.row.<person column>}}"}` or `{"kind": "manager_of", …}` (that person's team lead; when the lead
is the one who started the rule, it goes ahead without asking). A button rule with `trigger.data` only
runs from a row of that table.

1.0.56 adds what Site Schedules and IFRS 16 needed (`tools/site-schedules` and `tools/ifrs16` are the worked examples):

| Key | Where | Meaning |
| --- | --- | --- |
| `{"type": "action", "action": <job>, "data", "row", "in": {param: column or {{template}}}, "out": {result: column}, "as"}` | rule step | runs one of the **server jobs** Core offers (`cbx/engine/actions_core.py`; the Builder lists them: *Read a Swiftnet quotation* …). A file goes in from a file column; what comes back is written into the row (a file into a file column, a list of lines into a checklist) and is `{{<as>.<result>}}` afterwards. A job may need a tool grant or a role of the person the rule runs as. A *slow* job runs in the background: the rule waits ("working on it") and carries on when it is done; the page shows the row when it is |
| `"from": {"data", "row"}`, `"fail": {column: value or "{{error}}"}` | action step | the job takes its files from another row (a month takes the source files of its upload); when the job does not finish, the row says so (Status Failed, and why) |
| `{"type": "make_doc", "template": "<x>.docx", "data", "row", "into": <file column>, "name"}` | rule step | fills a Word template from the folder's `assets/` with the row — `{{column}}` (money as R 1 250.00, dates as 12 March 2026, people and linked rows by name) and `{{today}}` — and puts the document into the row |
| `{"type": "require", "when": [...], "match", "message"}` | rule step | *stop unless …*: the rule ends, failed, with that sentence (the person sees it) |
| `max_mb` up to 100 | file column | big workbooks (IFRS 16's source files) |
| `format: "money"` | number column | shown (and put into documents) as R 1 250.00 |
| `add: false` | table, board | no Add button there (rows come from a form or a rule) |
| `ask[].type: "month"` | button on a row | asks for a month (2026-08) |

A `.docx` may be uploaded to `assets/` (a Word template; always downloaded, never shown).

1.0.57 — **PDF Studio as a server job.** *Read a PDF with PDF Studio* (`pdfstudio.read`: the PDF from a
file column, and one of PDF Studio's templates, picked from a list) hands the PDF to PDF Studio as a batch
of one under the person's name; the template's columns come back into the row, with *How sure*, *What to
check* (what PDF Studio wants a person to look at), the batch number and the results as Excel. The batch
stays in PDF Studio: answering its questions there teaches the profile, and *Fetch what PDF Studio now
knows* (`pdfstudio.fetch`) brings the corrected values back. In the Builder, **New tool → It starts from:
PDFs read with PDF Studio · <template>** makes the whole tool (table, form, rules, button) in one go. A
job's input can be a pick-list (`choices`), and what it gives back can depend on it (`outputs_by`).

1.0.59 — **Other systems may read a table, with a key.** `data/<table>.json` may say
`"expose": {"columns": ["title", "lead"]}` (Builder: the table → *Other systems may read it*). No file
column may be listed. That alone opens nothing: the owner makes a key for that one table (Settings → API
connections → *Keys for our data*) with who it is for, an end date (90 days unless said, a year at most),
calls a minute and, if wanted, the addresses it may come from. The other system reads
`GET /api/shared/<tool>/<table>?limit=100&after_id=0` with `Authorization: Bearer cbxk_…` — the exposed
columns only (a person or link column as its name/label), every row (a key is not a person), oldest first,
with `next_after_id` for the next call. Reading only. Keys come in a header, never in the address, over
HTTPS (`CBX_DATA_KEYS_HTTPS_ONLY`); only the key's SHA-256 is kept, so it is shown once. C-EXA's teams
never write `expose`. (Calling an *outside* API is the other half and was already there: an API
connection, used from a page's API source or a rule's *Call an API* step.)

A Table block's `filters` may list the table's choice, link and yes/no columns. Flows are checked
against the choices: a Find rows or Add row value that is not one of the options is refused when the
folder is saved (a `{{template}}` value is checked when the flow runs dry).

### Pages for some roles

A page with `"access": {"roles": ["owner", "admin"]}` is only for those roles: it is not in anyone
else's tabs, its address answers *not for your role*, and so do the tables it shows (read or written,
straight or through another page) and its Run buttons — unless another page they may open shows the
same table. A page without `access` is for everyone who may use the tool. In the Builder: the Page
block → *Who may open this page*.

### Pictures

`assets/` holds what Image blocks show: png, jpg, gif, webp, svg or pdf, 5 MB each, uploaded from the
block's settings. They travel with the folder in every release.

### Words

Blocks never hold a word — they hold keys. `languages/en.json` has every key; `ar`, `pt`, `fr` carry
the same keys. A key a language lacks shows the English. The renderer draws Arabic right to left and
formats dates and numbers per language. People pick their language in Settings → Appearance.

## 2. From your screen to live

```
Builder (draft)  →  Preview  →  Release (a version, tested in the sandbox)  →  Promote (10-minute notice)  →  live
data\builder\tools\<slug>\        data\versions\<tag>\                  tools\<slug>\ in the live app
```

1. **Admin → Builder.** Open a tool at the top, or *New tool* (you give it a name; nothing else).
   Add parts to the page, answer their questions. *Save draft* writes the folder to
   `data\builder\tools\<slug>\` — nothing live changes. What to fix is listed bottom left.
2. The middle **is** the preview: the pages as people will see them, with live rows, in any of the four
   languages — unsaved changes included (code blocks run from the saved draft).
3. **Release** copies the live app, lays every draft into its `tools\`, gives it the next version number
   and stages it. The sandbox runs the whole test suite, the tool folders' own tests included. A tool
   without a hand-written test gets a generated one (the folder is sound, its pages answer).
4. **Versions & sandboxes → Promote.** Choose the notice: ten minutes by default. Every open page shows
   the countdown, carries what people are typing over the refresh, and lands them back where they were.
   *Switch now* for a security fix. *Cancel the update* before the switch and nothing has changed.
5. After the restart the engine reads `tools\` again and the folder is live. The draft can be discarded.

Nothing an AI does can shortcut this: C-EXA cannot promote, cannot grant access, cannot write to live.

## 3. Your first tool, step by step: utility bills from the warehouse

Ten minutes, no code. A page of the unpaid municipal bills PDF Studio has read, a chart per
municipality, and a button that reminds you of every unpaid one.

1. Admin → **Builder** → **New tool** → *Utility bills* → *Start*. The page is empty and the right side
   asks *What goes here?* → **Table**.
2. *Take the data from* → **Warehouse** → the master sheet (e.g. *Municipal bills*). The rows appear.
3. *Columns*: click the ones you do not want away. *Only rows where* → *+ add a condition* → Status ·
   is · Unpaid. *Sorted by* → Due. *Title* → Unpaid bills.
4. Hover under the table → **+ add here** → **Chart** → Warehouse → the same sheet → *One bar per*
   Municipality → *The total of …* Amount.
5. Left → **+ a rule**. Name it *Remind me of unpaid bills*. *ROWS* → *+ go through the rows* → the sheet
   → only rows where Status is Unpaid. *THEN* → *Tell someone* → the tool's owner → title
   `Unpaid: {Site}`, message `R {Amount} was due {Due}.` → **Try it**: it lists who would be told what.
6. Click the page → **+ add here** above the table → **Button** → *What it starts* → your rule.
7. The tool → *Name, menu and who uses it*: who may use it, and your email as the accountable person.
   Switch the rule **On**. **Save draft**; bottom left says *Sound*.
8. The tool → **Release** → *Package and test*. Then **Versions**: promote with the ten-minute notice.
9. After the switch: *Utility bills* is in the menu. Its folder is `tools\utility-bills\`: `tool.json`,
   `pages\main.json`, `flows\rule.json`, `languages\` — open them and read them; that is the whole tool.

## 4. Providers — where Core's data comes from

| Provider | Gives | Roles |
| --- | --- | --- |
| `compliance.grid` | the Entity Compliance dot matrix, worst company first, as the person may see it | anyone with the tool |
| `compliance.documents` | one row per live document with expiry and status; filter by status | anyone with the tool |
| `core.my_requests` | the person's own requests, newest first, with a link to each | anyone |
| `core.my_batches` | the person's PDF Studio batches: status, PDFs, cells for them | needs PDF Studio |
| `core.attention` | what the server noticed and nobody dealt with | owner, admin |
| `core.sign_in_attempts` | failed sign-ins per email: attempts, last time, last IP, registered or not; filters registered / days | owner, admin |

A provider is a small Python function in `cbx/engine/providers.py` that returns one of two plain shapes.
New providers are code (Core); new tools are folders (yours).

### Home is a folder too

`tools/home/` is the dashboard: `pages/owner.json`, `admin.json`, `manager.json`, `user.json`, each rows
of **Numbers** blocks and short lists. Open it in the Builder (Home → *Design this Home*), tick numbers,
release. A block id must be unique across the folder's pages (the canvas has one block per id), so the
Home pages name theirs `owner_today`, `user_mine`… People's own rearrangements live with them, not here.

## 5. What the tests hold you to

`tests/test_engine_tools.py` runs on every release: every folder is sound; every folder has a README
and a test; a folder over 40,000 tokens or a word without its four translations is a **warning** until
`CBX_STRICT_TOOLS=1` is set in the release environment — then it fails the release. The Entity
Compliance folder is about 5,000 tokens.

## 6. Where is what (this release)

| Thing | Where |
| --- | --- |
| The engine | `cbx/engine/loader.py` (reads and checks folders), `store.py` (one SQLite file per tool), `providers.py` |
| Its API | `cbx/api/engine_api.py` (menu, pages, rows), `cbx/api/builder_api.py` (drafts, preview, release) |
| The renderer | `web/views/tool.js` (pages → blocks); the renderer's own words `web/lang.js` |
| The Builder | `web/views/builder.js` (the screen: outline, page, save, release), `web/builder/ask.js` (the questions for every part, page, table, rule and the tool), `web/builder/rules.js` (a flow ↔ its plain rule) |
| Code blocks | served by `engine_api.code_block` with its own policy (`CODE_CSP`), framed with `sandbox` |
| Pictures | `tools/<slug>/assets/`; served by `engine_api.asset`, uploaded by `builder_api.upload_asset` |
| The notice | `cbx/versions/rollout.py` (announce, cancel, switch), `/api/version` in `cbx/main.py`, `web/update.js` (the bar), `web/carry.js` (what is carried over) |
| Flows | `cbx/engine/flows.py` (checks, runner, schedule tick), `cbx/api/flows_api.py` (Run, runs, approvals), rules in the Builder (`web/builder/rules.js`), `web/views/runs.js` (Approvals) |
| Drafts | `data\builder\tools\<slug>\` |
| Tool data | `data\tools\<slug>.sqlite` |
| An announced update | `data\PENDING_UPDATE.json` until it switches |
