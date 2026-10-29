# CBX-Connect — the owner's document

Version 1.0.114 · compiled 26 October 2026, 17:58 for the release zip (people, releases and backups are on the live page).

Compiled by the server from what it holds; regenerated every release. Owner only. It never holds a password, key or token.

## Where is what

| What | Where |
| --- | --- |
| The app (code) | the folder you unzipped (next to run.bat) |
| The data folder | data\ in the app folder, or CBX_DATA_DIR |
| The main database | SQLite: data\cbx.sqlite3 |
| Each tool's tables | data\tools\<tool>.sqlite; files on rows in data\tools\_files\ |
| Uploaded documents (the warehouse) | data\warehouse\ |
| The key that seals stored secrets | data\secret.key — back it up with the data; without it sealed secrets cannot be read |
| Backups of the data | CBX_BACKUP_DIR, default data\backups\data; every night at 01:30, 14 days kept |
| Backups of the app at each promote | data\backups\<version>-<time>\ (what Roll back uses; the database copy is in its database\ folder) |
| Settings | .env next to run.bat (never overwritten by a release); branding and API connections in the app |
| Tool folders | tools\ in the app folder; Builder drafts in data\builder\tools\ |


## Built-in screens

| Screen | Who | What it is for |
| --- | --- | --- |
| Home | Everyone | A dashboard per role; each person arranges their own (Customise). |
| Help | Everyone | Lessons and how-tos. |
| Requests | Everyone | Ask for something; follow what was asked; results. |
| Messages | Everyone | Chat with people and with C-EXA. |
| Approvals | Everyone | What a rule is waiting for you to approve; every run of every rule. |
| Work | Owner, admins | The request queue and the build tickets. |
| Builder | Owner, admins | Make and change tool folders: pages, tables, rules, words; release them. |
| People & access | Owner, admins (managers: their team) | People, roles, teams, grants, two-step, View as. |
| Audit trail | Owner, admins | Everything anyone (and C-EXA) did, append-only. |
| Versions | Owner, admins | Upload a release, test it, promote it with the ten-minute notice, roll it back; this document. |
| C-EXA | Owner (the board: read-only) | Talk to C-EXA, her teams and their jobs, C-EXA as developer. |
| Settings | Everyone (their own); the owner the rest | Appearance, language, security; mail, API connections, warehouse, branding. |
| PDF Studio | People with the PDF Studio grant | Read batches of PDFs with templates and learnt profiles; answer its questions; Harvest; master sheets. |
| Entity Compliance screens | People with the grant | Behind the Entity Compliance folder: file and renew documents, companies, reminders. |

## The tools

Each tool is a folder under `tools\` (manifest, pages, data, rules, words in four languages, tests, README). The Builder edits a copy; a release puts it live.

| Tool | Folder | Owner | Who uses it | Pages | Tables | Rules | State |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Entity Compliance | entity-compliance | edwin.botha@cbreexcellerate.com | owner, admin, grant doc-watch | 4 | 4 | 0 | sound |
| Home | home | edwin.botha@cbreexcellerate.com | everyone | 4 | 0 | 0 | sound |
| IFRS 16 | ifrs16 | edwin.botha@cbreexcellerate.com | grant ifrs16 | 4 | 5 | 5 | sound |
| Site Schedules | site-schedules | edwin.botha@cbreexcellerate.com | grant site-schedules | 1 | 1 | 3 | sound |
| To-do | todo | edwin.botha@cbreexcellerate.com | grant todo | 5 | 6 | 7 | sound |


### Entity Compliance (`tools\entity-compliance`)

**Tables** (rows in `data\tools\entity-compliance.sqlite`)

| Table | Columns | Who sees the rows | Who adds |
| --- | --- | --- | --- |
| companies | name, responsible, responsible_account, responsible_email, visible, notes, archived, archived_reason | members | managers |
| company_people | company, person, note | linked | admins |
| doc_types | name, short, order, warn_days, shown | all | admins |
| documents | company, doc_type, title, file, expiry, no_expiry, warn_days, required, status, days_left, notes, filed_by, archived | linked | everyone |


**Its README**

**What it is.** The compliance register drawn by the engine: companies down the side, document types across the top, one dot per cell; and a flat list of every live document. Same data, same rules as the built-in Entity Compliance screens. Since 1.0.56 it is the one Entity Compliance menu row: clicking a dot opens the built-in screen to file or renew that document, and *Companies and settings* opens the rest (companies, reminders, archive).

**Who sees it.** Owners and admins always (`access.roles`); anyone else who has been granted the Entity Compliance tool in Users & roles (`access.tool` = `doc-watch`, the grant's technical name). What a person sees inside is still filtered by the same rules as before: their companies, their team's, or everything for admins.

**Where the data lives.** Nowhere new. Both pages use providers (`compliance.grid`, `compliance.documents`) that read the compliance tables Core already keeps. This folder has no `data/` blocks of its own yet.

**Pages.**

| Page | Type | Source | Shows |
| --- | --- | --- | --- |
| `grid` | grid | provider `compliance.grid` | the dot matrix, five summary tiles, the legend |
| `documents` | table | provider `compliance.documents` | one row per live document; search; filter by status |

**Export (1.0.99).** The grid page's *Export* button (a `links` block, `w_export`) downloads `Entity Compliance <date>.xlsx`
from Core's `GET /api/ec/export` (`cbx/api/ec_export_api.py`, book in `cbx/docwatch/ec_export.py`): only the companies the
person may see, through these tables' own scopes; archived ones on their own sheet, for admins only. Sheets: Overview,
Documents, Expiring and expired, Entities, Document types, About. Audited as `ec_export` (counts only).

**Words.** Every label is a key in `languages/en.json`; `ar`, `pt` and `fr` carry the same keys. A key a language lacks falls back to English. Arabic pages are drawn right-to-left by the renderer.

**How to change it.** Admin → Builder → Entity Compliance. Change a label, add a column to the documents table, or change who sees it; preview; save the draft; release. The release runs the tests, you sign it off on Versions, and the ten-minute notice goes out. Nothing here is code.

**Tests.** `tests/test_entity_compliance.py` in this folder (one file per tool, named after it): the folder is sound, the pages answer for an owner, and a person without the grant is refused.


### Home (`tools\home`)


**Its README**

**What.** The first page everyone sees: rows of numbers (open requests, documents due, failed sign-ins,
cells to check…) and one or two short lists. Click a number to open the page behind it.

**Who.** Everyone (`access.everyone`). It is not in the menu (`menu.hidden`) — it *is* Home. Each person
gets the page for their role: `owner`, `admin`, `manager`, `user`. A role invented in Users & roles
gets the admin page if it is an admin-level role, the manager page if it manages people, else `user`
— or its own page, if you add one named after it.

**Where.** `pages/<role>.json`. Each **Numbers** block (`type: metrics`) lists metric keys; the
catalogue is `cbx/engine/metrics.py` (the Builder lists it with a line on each). A person only sees
the numbers their role and tools allow — the others are not shown to them, not shown as zero.

**How to change it.** Admin → Builder → Home: tick numbers in a Numbers block, add blocks, wire them to
a role's page, Save draft → Preview → Release → Promote. That changes the starting design for the
role. Each person can then rearrange their own Home (Home → Customise: add, remove and drag numbers,
hide a block, Reset); that is kept with them, in their settings, and never touches this folder.

**Owner.** edwin.botha@cbreexcellerate.com


### IFRS 16 (`tools\ifrs16`)

**Tables** (rows in `data\tools\ifrs16.sqlite`)

| Table | Columns | Who sees the rows | Who adds |
| --- | --- | --- | --- |
| api | kind, month, to_month, status, ok, result, download, note | mine | everyone |
| ledger | ledger, as_at, months_back, status, report, overview, note | mine | everyone |
| months | month, upload, source, status, imports, workbooks, report, papers, recon, note | mine | nobody |
| rates | month, a, b, c, d, e, f, note | all | everyone |
| uploads | summary, la, movements, sequentra, steps, assets, ciw_master, movement, months, latest, status, note | mine | everyone |


**Rules**

| Rule | When | On/off | Runs as | Server jobs |
| --- | --- | --- | --- | --- |
| Check the Sequentra API | row_added on api | on | starter | Check the Sequentra API |
| Download every Sequentra report | row_added on api | on | starter | Download every Sequentra report |
| Check a ledger | row_changed on ledger | on | starter | Check an IFRS 16 ledger |
| Work out a month | button on uploads | on | starter | Work out an IFRS 16 month |
| Read new source files | row_changed on uploads | on | starter | Read the IFRS 16 source files |


**Its README**

**What it is.** Monthly Lease Accelerator imports from the RR06 / ARO06 movements and the LA register, and the ledger check. Made in the Builder only (1.0.56); the lease engine itself (`cbx/ifrs16/engine`, unchanged) is run as three server jobs. It replaces the built-in IFRS 16 page; its kept uploads, months and ledger checks were copied in once, with their files.

**Who sees it.** Anyone granted IFRS 16 in People & access (`access.tool` = `ifrs16`); owners and admins always. Each person sees their own uploads, months and checks; admins see all.

**The tables.** `uploads` (the four source files — up to 100 MB each — what was read, the months in them, Status New · Reading · Ready · Could not read, a note) · `months` (one per month worked out: the import workbooks in one zip, the monthly report, how many import workbooks, the engine's reconciliation lines, Status Working · Done · Failed; only the rule adds them) · `ledger` (the ledger export, as at, months back, the consolidated report and its overview).

**The pages.** *Monthly workflow*: a form for the source files, the kept uploads with **Work out a month** (it asks which month), and the months. *Ledger check*: a form for the export and the list of checks.

**The rules** (all on):

| Rule | When | What |
| --- | --- | --- |
| Read new source files | an upload's row changes, both required files are there, and it is New | Status Reading → the job *Read the IFRS 16 source files* (background) fills in the months → Ready; if it cannot read them: Could not read, and why |
| Work out a month | the button on an upload (asks the month) | stops unless the upload is Ready and holds that month; adds the month's row; the job *Work out an IFRS 16 month* (background, taking the files from the upload) puts the zip and the report in it → Done, or Failed and why |
| Check a ledger | a check's row changes (its export arrives) and it is New | Working → the job *Check an IFRS 16 ledger* (background) → Done, or Failed and why |

The page shows the row as soon as a background job is done.

**Tests.** `tests/test_ifrs16_folder.py`: the folder is sound; files are read and a month worked out (report and workbooks); unreadable files say so on the row; the ledger check; people see their own and nobody adds a month by hand.


### Site Schedules (`tools\site-schedules`)

**Tables** (rows in `data\tools\site-schedules.sqlite`)

| Table | Columns | Who sees the rows | Who adds |
| --- | --- | --- | --- |
| quotes | quote, more, status, site_name, company, lease_type, start_date, term_years, term_months, esc_year, col_id, contract_no, soc_no, t100_ref, t000_ref, co_code, lease_code, swiftnet_rep, couser_rep, stac, pid, region, lat, lon, struct_name, struct_cat, site_owner, co_site_name, bs_site_no, co_region, mast_cat, mast_hagl, inst_hagl, scope, equip_comments, monthly, charges, read_from, checks, checks_n, checks_done, swift_signers, couser_signers, details, agreement | mine | everyone |


**Rules**

| Rule | When | On/off | Runs as | Server jobs |
| --- | --- | --- | --- | --- |
| Make the agreement | button on quotes | on | starter | Make the Site Schedule Agreement (Word) |
| Read the new PDFs | row_changed on quotes | on | starter | Read the Swiftnet PDFs (Site Schedule Agreement) |
| Read the PDFs again | button on quotes | on | starter | Read the Swiftnet PDFs (Site Schedule Agreement) |


**Its README**

**What it is.** A Swiftnet co-location quotation in, the Site Schedule Agreement out — a Word document, ready to sign. Made in the Builder only (1.0.56); it replaces the built-in Site Schedules page. The quotations read before were copied in once, with their PDFs and agreements.

**Who sees it.** Anyone granted Site Schedules in People & access (`access.tool` = `site-schedules`); owners and admins always. Each person sees the quotations they added; admins see all.

**The table** `quotes`: the quotation (PDF), site, quote number, province, equipment accommodation, antennas, the fees (shown as money), *What to check* (a checklist), how many lines to check / checked, Status (New · Read · Built), the agreement (Word).

**The page.** A form with only the quotation file (*Read it*), a short how-to, and the quotations with two buttons on each row: *Build the agreement* and *Read it again*.

**The rules** (in `flows/`, all on):

| Rule | When | What |
| --- | --- | --- |
| Read a new quotation | a row changes (its PDF arrives) and it is New | the server job *Read a Swiftnet quotation* fills the row; what it is unsure of becomes lines under *What to check*; Status Read |
| Read the quotation again | the button | the same, on purpose (what was corrected is replaced) |
| Build the agreement | the button | stops unless the site is filled in and every line is ticked (and says so); makes the Word document from `assets/site_schedule.docx` into the row; Status Built |

**The template.** `assets/site_schedule.docx` — Word, with `{{site}}`, `{{quote_no}}`, `{{province}}`, `{{equipment}}`, `{{antennas}}`, the fee columns and `{{today}}` where the row's values go. Replace it in the Builder (the rule's *Upload a template…*) with the company's own; keep the `{{…}}` names.

**Tests.** `tests/test_site_schedules_folder.py`: the folder is sound; a quote is read, checked and built into a Word document with the Rand amounts; someone without the tool is refused.

1.0.88: the engine is CBX-Connect 2's (`cbx/schedules/agreement.py` over `reader.py` and `template.py`): the quote and the NTP or an earlier agreement in, every comment its own charge line, the row holds every field of the agreement, and **Make the agreement** writes the Word document like the real one.


### To-do (`tools\todo`)

**Tables** (rows in `data\tools\todo.sqlite`)

| Table | Columns | Who sees the rows | Who adds |
| --- | --- | --- | --- |
| goals | title, target, size, starts, ends, reward, counts | team | managers |
| job_members | job, person, status | members | managers |
| job_tasks | job, title, who, status, steps, due | members | everyone |
| jobs | title, purpose, lead, due, status | members | managers |
| reward_days | goal, person, day | team | nobody |
| tasks | title, kind, status, who, steps, steps_n, size, due, notes, finished, done_by | mine (+ team where kind is Team) | everyone |


**Rules**

| Rule | When | On/off | Runs as | Server jobs |
| --- | --- | --- | --- | --- |
| Book my reward day | button on goals | on | starter | — |
| Mark it done | button on tasks | on | starter | — |
| Note when a task is done | row_changed on tasks | on | starter | — |
| The lead is on the job | row_added on jobs | on | starter | — |
| Ask the team lead before someone joins a job | row_added on job_members | on | starter | — |
| Clear the finish date when a task is reopened | row_changed on tasks | on | starter | — |
| Take it | button on tasks | on | starter | — |


**Its README**

**What it is.** Your own to-do list, the team's board, jobs across teams, stats, and the goals a team lead sets with a reward day when you reach one. Made in the Builder only — tables, pages, blocks and rules; no code of its own. It replaces the built-in To-do page (1.0.55). The old tasks, jobs, goals and reward days were copied in once, with who added them and when.

**Who sees it.** Anyone granted To-do in People & access (`access.tool` = `todo`); owners and admins always.

**The tables** (in `data/`, rows in `data\tools\todo.sqlite`):

| Table | Holds | Who sees the rows | Who adds |
| --- | --- | --- | --- |
| `tasks` | every task: For (Just me / The team), Status, Who, Steps, Size (from the number of steps: up to 3 Small, up to 7 Medium, more Complex), Due, Finished, Done by | each person their own; the whole team sees the rows where For is The team; a manager also sees their team's | everyone |
| `jobs` | a job: Title, What it is for, Lead, Due, Status | the job's active members, and whoever added it | managers |
| `job_members` | who is on which job, and whether their team lead said yes (Requested / Active / Declined / Released) — each person once per job | the same job's members | managers |
| `job_tasks` | the tasks of a job | the job's members | everyone |
| `goals` | a team lead's goal: Target (tasks), Size (empty = any), From, To, Reward, Who counts (each person, or the team together) | the team | managers |
| `reward_days` | one booked day per goal and person | the team | nobody by hand — only the Book rule, once the goal is reached |

**The pages.** My to-do (my list, team tasks I'm on, finished this month) · Team board (drag cards; Open to take with a Take it button) · Jobs · Stats (this week, this month, open; by size; per person) · Goals (where I stand on each person's goals; where the team stands on the team's — its team tasks done together; Book my reward day once reached).

**The rules** (in `flows/`, all on):

| Rule | When | What |
| --- | --- | --- |
| Take it | the button on an open team task | only if nobody has it: Who = me, Status = Doing |
| Mark it done | the Done button | Status Done, Finished today, Done by me |
| Note when a task is done | a task changes (e.g. dragged to Done) | if Done and no finish date: Finished today, Done by the person |
| Clear the finish date … | a task changes | if not Done but it has a finish date: clear it |
| The lead is on the job | a job is added | its lead joins as Active |
| Ask the team lead … | someone is added to a job | the person's team lead approves (Active) or declines (Declined); a lead adding their own team's people is not asked |
| Book my reward day | the button on a reached goal (asks: which day?) | adds the reward day; tells the team lead |

**How to change it.** Admin → Builder → To-do. Everything above is a question on the right-hand side; nothing here is code.

**Tests.** `tests/test_todo_folder.py`: the folder is sound; the Done button and stats; personal rows stay personal, team rows are shared and taken once; a reward day only once reached and only once; jobs are for their members and the member's team lead says yes.


### Server jobs the rules may run

| Job | Group | Background | What it does |
| --- | --- | --- | --- |
| Check an IFRS 16 ledger | IFRS 16 | yes | Checks a ledger export against what the leases should show, as at a month and a number of months back, and writes the consolidated report. |
| Check the Sequentra API | IFRS 16 | yes | Tests the Sequentra connection (Settings → API connections) and calls each of its ten reports once: how many rows and fields came back, or why not. No lease data is kept. |
| Download every Sequentra report | IFRS 16 | yes | Every Sequentra report as it arrives, one CSV each, with a summary and a field list, zipped — to see what the reports hold before the engine reads them. |
| Read the IFRS 16 source files | IFRS 16 | yes | Reads the LA register and the RR06 / ARO06 movements (and the Sequentra export and step payments, if given) and lists the months the movements hold — the months a workflow can be run for. |
| Work out an IFRS 16 month | IFRS 16 | yes | Runs the IFRS 16 lease engine for one month from the kept source files — or with the lease facts from the Sequentra API — and writes the Lease Accelerator import workbooks (in one zip) and the monthly report, with the engine's reconciliation lines. SA leases take their discount rate from the tool's Rates table. |
| Fetch what PDF Studio now knows | PDF Studio | no | After someone answered PDF Studio's questions about a batch, brings the corrected values back into the row. |
| Read a PDF with PDF Studio | PDF Studio | yes | Hands the PDF to PDF Studio with one of its templates (the columns it reads, and the profile it has learnt). The template's columns come back into the row, with how sure it is and what a person must look at; the batch stays in PDF Studio, where answering its questions teaches the profile. |
| Make the Site Schedule Agreement (Word) | Site Schedules | no | Makes the Site Schedule Agreement in Word, laid out like the real one, from what was read and the row's fields as a person corrected them. Signing people: one per line, 'Name, Title'. |
| Read a Swiftnet quotation | Site Schedules | no | Reads the site, quote number, province, the monthly and once-off fees and the antennas from a Swiftnet co-location quotation PDF. Anything it is unsure of becomes a line to check. |
| Read the Swiftnet PDFs (Site Schedule Agreement) | Site Schedules | no | Reads a Swiftnet quotation, and the NTP or an earlier signed agreement if given, the way CBX-Connect 2 does: the site, the co-user, the dates and every charge line (each numbered comment its own line). What it could not find becomes a line to check. |


## Open to other systems

Other systems read a table only with a key the owner makes (Settings → API connections → Keys for our data): the columns listed here, reading only, never files, over HTTPS.

No table is open to other systems.

## How to

### Change a tool

1. Admin → Builder → choose the tool at the top.
2. Click a part on the page to change it, or use the outline on the left (Pages, Data, Rules, The tool).
3. Save draft (Ctrl+S). The foot of the outline says what to fix before a release.
4. Words in 4 languages: fill the other three, or press Let C-EXA translate the missing words and read them.
5. Release… → it becomes a version on the Versions page (below).

### Release a version

1. Admin → Versions → Upload & test (a cbx-online-<version>.zip, or a Builder release).
2. The tests run in a sandbox; the council (C-EXA's Governance team) reviews the change.
3. Test in sandbox opens it in a copy of the app if you want to look first.
4. Promote → everyone signed in gets a ten-minute notice, then the server restarts on the new version. Your .env is never overwritten.
5. Only upload versions you trust. Their tests run on this server under the server's own Windows account: since 1.0.114 they no longer see the server's settings and secrets in the environment, but that account can still read the .env file, data\secret.key and the live database from disk. The real fix is an IT step: run the tests under a separate Windows account with low rights that cannot read the CBX data folder or .env.

### Roll back

1. Admin → Versions → the version that is live → Roll back. The live files are put back from the backup taken when it was promoted. From 1.0.114 the database is backed up beside them at every promote, and Roll back offers to put the data back too (off unless you tick it). Everything saved since the promote is replaced: people added since are gone and access given since is undone. Security changes made since are kept (disabled, locked or removed access stays so; passwords and two-step settings stay current), the audit trail since is copied back, and everyone is signed out. What it replaces is kept beside the backup.
2. Only the version before the live one can be rolled back to this way; for older ones restore a backup (below).

### Restore a backup (e.g. into a test copy)

1. Backups are made every night (see Backups below) into the backup folder; each night is a folder named by its date and time.
2. Into a test copy: make a new folder, copy the snapshot into it (cbx.sqlite3, tools\, secret.key, branding\, builder\), copy files\warehouse to warehouse\ and files\tool-files to tools\_files\, then start a second CBX with CBX_DATA_DIR set to that folder and another CBX_PORT.
3. Onto the live server: stop CBX (close run.bat), keep a copy of the data folder as it is, copy the snapshot over it the same way, start run.bat. People lose what they did after the snapshot.

### Unlock the owner

1. On the server, run unlock-owner.bat (in the CBX-Online folder, beside the app): it clears a locked owner account.

### Unlock someone, or reset their two-step

1. People & access → the person → Edit → tick Unlock; or Reset two-step (a reason is asked and kept).

### See what someone sees

1. People & access → View as (read-only, up to an hour, written to the audit trail).

### Switch a rule on or off

1. Builder → the tool → Rules → the rule → On / Off → Save draft → release.

### Let another system read a table

1. Builder → the tool → the table → Other systems may read it: tick the columns (never files) → Save draft → release.
2. Settings → API connections → Keys for our data → Make a key: who it is for, the table, how long (90 days unless you say; a year at most), calls a minute, and the addresses it may come from.
3. Copy the key when it is shown — it is shown once. The other system reads GET /api/shared/<tool>/<table> with Authorization: Bearer <the key>, over HTTPS.
4. Lost or leaving: Revoke. It stops at once. Every key made, refused or revoked is in the audit trail.

## The ten things (the acceptance)

|  | Where it is in this document |
| --- | --- |
| Sign in as owner and unlock a locked account. | How to → Unlock |
| Create a user, give them one company's folder only, then take it away. | People & access |
| Start dev on your box and open one tool on its own. | How to → Restore a backup (into a test copy) |
| Build the unregistered sign-ins tool in the Builder and release it. | How to → Change a tool, Release |
| Change a menu label in all four languages and release it. | How to → Change a tool |
| Roll back a release. | How to → Roll back |
| Restore last night's backup into dev. | How to → Restore a backup; Backups |
| Open any tool folder and say what each of its seven things does. | The tools |
| Ask C-EXA Developer for a change, review it, and reject it. | C-EXA → C-EXA as developer |
| Answer IT's questions from the owner's document alone. | this document |
