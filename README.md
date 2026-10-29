# CBX-Online

The Digital & Transformation platform for CBRE Excellerate. People submit requests and
PDFs; C-EXA (the AI controller) and the admins turn them into tools, flows and filled sheets; everyone
gets their results in one place. Every action — human or C-EXA — is in the audit trail.

Version 1.0.97 · Windows single-server install · Python 3.11+ · optional Ollama for C-EXA.

---

## 1. Install (once, on the Windows machine with the 5090)

1. Install **Python 3.11 or newer** from python.org. Tick **"Add python.exe to PATH"**.
2. Unzip `cbx-online-1.0.97.zip` into a folder, e.g. `C:\CBX\`.
3. Double-click **`setup.bat`**. It creates the Python environment, installs requirements, asks for
   your email and name, and prints your **temporary password**. Write it down.
4. Double-click **`run.bat`**. Leave that window open (minimised is fine). It prints the addresses
   to open: **`http://localhost:8080`** on the server itself, and `http://<server-name>:8080` or
   `http://<server-IP>:8080` from any other PC on the network. (Don't type `0.0.0.0` — that is the
   "listen on all network cards" setting, not a page.) If Windows Firewall asks, allow Python on
   private networks; otherwise colleagues can't reach it.
5. Sign in with your email and the temporary password. You'll be asked to set your own.

### C-EXA's brain (Ollama) — do this next
Install Ollama from ollama.com, then in a command window:

    ollama pull qwen3.8:27b
    ollama pull qwen3.5:9b
    ollama pull glm-ocr

Restart `run.bat`. The Talk-to-C-EXA page shows **"qwen3.8:27b · online"** when she's thinking for
real. Until then the app runs in *rules-only* mode: PDF Studio still reads bills with its rule
engine, requests still get scored, but C-EXA's replies are fixed sentences.

**How hot C-EXA runs** (`.env`): `CBX_TEMP_VOICE=2.0` is how she talks (chat replies, briefs — lively),
`CBX_TEMP_CODE=0.0` is anything that becomes code or a workflow (the same exact answer every time),
`CBX_TEMP_DATA=0.1` is reading PDFs, scoring and choosing tools. Decisions always run cold; only the
sentence you read is re-voiced hot, and every number and name in it is checked against the cold draft.

**Is she fast?** The model must sit **100 % on the GPU**. If C-EXA takes a minute per answer, Home →
*Needs attention* says so in one line ("qwen3.8:27b is 62% on the GPU — 38% is running on the CPU…") and
you can ask her *"why are you slow"*. The usual fix is `CBX_NUM_CTX=16384` in `.env` (the context window
CBX asks Ollama for; default 32768 — smaller frees video memory) or the `qwen3.8:27b-nvfp4` tag, then
`server-tools\restart.bat`. Details: `docs/cexa-speed.md`.

### Scanned PDFs (OCR)
PDFs with no text layer (scans) need **Tesseract OCR**. Windows installer:
https://github.com/UB-Mannheim/tesseract/wiki — install to the default folder and set
`CBX_TESSERACT_CMD` in `.env` (see `.env.example`). Without it scanned pages come to the user as
misses instead of being read.

### Flows (was Node-RED)
Since 1.0.48 CBX has no Node-RED. What it did — "when this happens, do these steps" — is a **flow**
inside a tool folder, built on the Builder's **Flows** tab and run by CBX itself: under the same
sign-in, access rules, audit trail, tests and releases as everything else. See *Flows and Jobs
(1.0.48)* below and `docs/tool-folders.md`. If the server still starts Node-RED at logon, Home says
so: run `server-tools\remove-node-red.bat` as administrator once.

### API connections (keys and addresses for outside services)
**Admin → API connections** is where an outside API is attached to a tool: a name, the service's
address, how the key is sent (bearer, header, basic, query) and the key itself. The key is stored
sealed with the server's own `data\secret.key` and is never shown again — only its last four
characters. **Test** makes one call to see the service answers. **Tick the tool folders that may use
it**: a page's API source or a flow's *Call an API* step in any other tool is refused ("not open to
this tool"). CBX adds the key on the server and makes the call, so no page and no flow ever holds the
key. Every change and every call is in the audit trail (without the key).

### Two-step verification — `docs/two-step.md` (what each person sees, the reset, the day you promote it, the `.env` keys).

### Going live — `docs/go-live.md` (firewall, start at logon, nightly backup, graceful restart: `server-tools.zip`; remote access via Tailscale; why not to hand-edit files on the server).

### OneDrive mirror — `docs/onedrive-setup.md` (needs a 10-minute app registration from IT).

---

## 2. Who sees what

| Role | Sees | Can |
|---|---|---|
| **User** | Home, their tools (PDF Studio, Master Sheets, Entity Compliance, IFRS 16 — as granted), New request, Messages, My requests, Results, Settings | Submit requests, run their tools, fill in cells C-EXA couldn't read |
| **Manager** | The user's pages, plus **Users & roles**, **Audit trail** and **Entity Compliance** limited to *their own team*, and "Ask C-EXA" (answers about their team only) | Add **users** to their team, edit / unlock / reset them, grant the tools the manager holds. Cannot change roles or teams, cannot see other teams, admin pages or model detail |
| **Admin** | + Requests queue, Develop, Users & roles (everyone), Builder, Data warehouse, Audit trail (everyone), "Ask C-EXA" with hands | Approve requests, interview, build workflows, add **managers and users** |
| **Owner** | + Talk to C-EXA, Agents, C-EXA as developer, Versions & sandboxes, Settings → Branding | Everything, incl. making owners/admins/managers, promoting versions |
| **C-EXA** | Everything (read) | Everything except: grant access, promote to production, approve profile patches — those need a person |

A **manager** is deny-by-default like a user: tools by grant, the user's Home, no model names or scores.
What makes them a manager is scope — the people, audit entries and register lines of the team they sit
on (owners and admins on the same team are not theirs to see or edit). Managers on a team also get the
Entity Compliance reminders for that team's documents.

**To-do** (a tool, by grant): each person sees only their own to-do; the team sees the team tasks (who
has which one, at what step) and the team's totals; the team lead — the team's manager, or an admin/owner
who picks the team — sees everyone's to-do, shapes the columns, assigns, sets goals and sees per-person numbers.

Add people from **Users & roles → Add user** (the second button offers whichever senior roles you
may hand out): you get a one-time temporary password to pass on; they replace it on first sign-in.
Forgotten password → they click "Ask the owner" and you see "Reset requested" next to their name →
**Temp password**. Five wrong attempts lock an account; Edit → Unlock.

### Roles you make yourself

The four roles above are built in and cannot be changed — 152 checks in the app rely on them, and an
owner who unticked the wrong box on **Owner** would lock the company out with no way back in. Everything
else is yours: **Users & roles → Roles & permissions** lists every role, what it can do, and how many
people hold it. **New role** builds one from a grouped list of capabilities, each with a plain sentence
saying what it opens; **Copy** starts from a built-in — that is how you make "an admin who can't touch
people" or "a manager for one team".

Three shipped ready-made, and you can edit or delete all three:

| Role | For | Notably cannot |
|---|---|---|
| **Developer admin** | Builds and tests the app: proposes code, reviews C-EXA's board, runs sandboxes | Touch people, promote to production |
| **User admin** | Accounts, roles and tool access | Change the app itself |
| **Viewer** | Looking: people, audit trail, org chart | Change anything at all |

Two rules are enforced on the server, not just on the screen. **You cannot give away a capability you
do not hold** — a user admin cannot quietly write "Promote a version" into a role and then assign it to
themselves; the boxes are greyed out and the save is trimmed. And **rank** decides who may hand a role
out: you can only create, edit or grant a role ranked below your own, so nobody promotes themselves or
a colleague past themselves. "Their own team only" is the exception — it takes access away rather than
adding it, so anyone building a role may tick it. The last role that can administer the system cannot
be emptied or deleted; a role somebody holds cannot be deleted until they are moved. Every change is
written to the audit trail.

### Messages — colleagues and C-EXA in one place

**Messages** holds two things. Under **People**: direct messages, your team's room (everyone on the
team is in it by being on the team), and rooms an admin or manager made. Under **C-EXA**: the interviews
about your requests and her notes to you. You can message people on your own team and every admin
and owner; admins can message anyone. A direct message is read by its two people and nobody else —
not an admin, not the owner. Admins may read team rooms, because they manage teams. Nothing is edited
or deleted. Address C-EXA by name in any conversation — `@C-EXA what's expiring this week` — and she
answers in the room a few seconds later, seeing exactly what the person who asked may see. She does
not read along silently and volunteer. Rules live in `cbx/chat/service.py`.

### Talking to C-EXA out loud

The C-EXA drawer and **Talk to C-EXA** have a microphone and a speaker. Click the mic and speak: the words
stream into the box as you say them, and you press Send — nothing is sent that you did not read
first. Click the speaker and C-EXA's replies are read aloud (remembered per browser). Both use what the
browser already has (Chrome or Edge). The microphone only opens on a **secure address** — https, or
localhost — so it works through the tunnel and not on the plain LAN address; the button says so
rather than failing quietly. Chrome does its recognition through Google's speech service; a Whisper
model on the server is the private path and the next step (`web/voice.js`).

### C-EXA's agents

**Agents** (owner) shows C-EXA's standing team — three of them, each doing one job, and only kinds that
real code runs as. **harvester** reads PDF batches. **dev** builds proposals in a sandbox. **watcher**
reads the audit trail every five minutes and never writes anything but its own notes: it alerts the
owners at once on a burst of failed sign-ins for one account, a person refused over and over, a server
error or a routine that died, and sign-ins going through unverified; and once a day, first thing, it
writes one plain paragraph on the day before. Everything it says lands in the owners' inbox from C-EXA
(Home → From C-EXA, Talk to C-EXA), is written to the audit trail as `watcher_alert` / `watcher_report`,
and appears as its notes on the Agents page. Nothing needs approving because nothing is changed.
`cbx/cexa/watcher.py`; it keeps its place (the last audit row read) in `app_state`, so a restart neither
repeats an alert nor skips one. The app now records its own failures as audit rows too — `server_error`
for a 500, `routine_failed` / `job_failed` for background work — which is what the watcher reads.

### C-EXA's hands in the sandbox (1.0.39)

The one rule that never changes: **C-EXA never edits the live app.** Every code change is a proposal →
the owner says go → a sandbox copy is built and tested → the owner promotes. What 1.0.39 adds is a
developer's hands *inside* that sandbox, so C-EXA can iterate instead of guessing once:

- **The build loops.** After the first patch the tests run; if they fail, C-EXA reads the failures,
  fixes the files and runs them again — up to three rounds, each a commit on the sandbox branch. It
  moves to sign-off only when green, and the board card says how many rounds it took. The diff the
  owner reviews is always against the copy of the live tree, however many rounds there were.
- **Tools in conversation** (owner/admin, once a proposal is being built): `sandbox_status`,
  `read_sandbox_file`, `search_sandbox`, `patch_sandbox_file` (exact passage, once), `write_sandbox_file`,
  `run_tests` (failures verbatim), `diff_sandbox`, `commit_sandbox`, `sh` and `run_sandbox_app` /
  `stop_sandbox_app` (the sandbox copy on its own port and empty database — the owner's playground:
  click through, then promote or say Not now). Every call is on the audit trail as `cexa_hands`.
- **What `sh` may run:** `pytest`, `ruff`, `git status/diff/log/add/commit/show`, `pip install -r
  requirements.txt`, `python -m pytest`. One plain command — no `&&`, `|`, `>`; no `python -c`, no
  scripts; no path that leaves the sandbox. The child gets no CBX settings and its own throwaway data
  folder. A sandbox waiting for sign-off is never edited.
- **Honest limits.** This is a *path* jail, not a *privilege* jail: a test C-EXA writes still runs as the
  server's user — exactly as the build always has. Running the sandbox under a separate low-rights
  Windows account (or a container) is the real fence and is on the IT list. `CBX_CEXA_HANDS=0` in
  `.env` switches every one of these tools off.
- **The pulse.** Every minute C-EXA checks Ollama and the model, failed model calls, stuck PDF batches,
  the routine loop itself, disk, database size, version drift (VERSION vs the running code — a hand
  edit outside the pipeline shows here), stuck builds and Node-RED. Two bad readings in a row → one
  alert to the owners (Home → From C-EXA, audit `pulse_alert`); one recovery note when it clears. Ask
  C-EXA `pulse` for the state now, `what_changed since yesterday` for what flipped and what the audit
  trail recorded; the morning brief ends with a Systems line.
- **Branding guard.** A version package that changes the product's or C-EXA's identity in the code is
  refused at upload unless its notes say "branding change" — names are settings (Settings → Branding),
  not code.

### Into the sandbox (1.0.40)

A running sandbox used to be reachable only from the server's own screen (`127.0.0.1:8081`), and its
database is empty, so the live password could not open it — nobody could actually click through a
build before promoting it. Two changes:

- **A sign-in of its own.** When a sandbox starts (Versions → *Start*, or C-EXA's build finishing), the
  package's own `bootstrap` creates every live owner in the sandbox database — same email, a fresh
  **sandbox-only password**, shown on that sandbox's card on Versions & sandboxes, new at every start,
  forgotten when it stops. Nothing of the live accounts is copied but the email and the name: C-EXA's
  code runs in there, and the live password hashes never leave the live database.
- **The door.** *Go in* on the card takes you to `/sandbox/<slot>/enter`: the sandbox then answers at
  **the same address you are already using** — the tunnel, the LAN name, whichever — with a brown strip
  at the top saying which sandbox it is and a **Back to live** link (`/sandbox/leave`). Two keys are
  needed at once: your CBX admin session and a signed routing cookie made for that session, so it
  cannot be minted, moved or outlive the sign-in. The live session and device cookies never reach the
  sandbox; the sandbox's own cookies come back renamed so they cannot overwrite the live ones. While
  the door is open, that address *is* the sandbox for that browser — the strip is there so nobody
  mistakes it for production. Audit: `sandbox_opened`, `sandbox_entered`, `sandbox_left`.
- Also: a version's tests no longer hold the database's write lock for their four minutes (other
  writers saw "database is locked" during an upload); the pulse skips a tick quietly when the database
  is busy; a proposal's card on Versions now points at its sandbox copy on disk.

### Hardening (1.0.41)

Ten findings from an outside audit of the live tree, all confirmed in the code and fixed; the tests
say what each one now does (`tests/test_hardening.py`).

- **Forwarded headers are believed only from a trusted proxy.** `X-Forwarded-For` / `-Proto` used to be
  taken from anyone; a new made-up address per request would have walked straight past the sign-in
  limits and put nonsense in the audit trail. Now only a peer in `CBX_TRUSTED_PROXIES` (default
  `127.0.0.1,::1` — the ngrok agent) is believed, and then the address *it* appended.
- **Uploads stop at the cap while being read**, instead of being read whole and measured after. Request
  attachments, branding images, workflow inputs and quotations now have a cap of their own
  (`CBX_MAX_ATTACHMENT_MB`, default 25).
- **Every script has a versioned address.** The shell loads `/static/<version>/…`, so a module's
  relative imports carry the version too; a promoted version has new addresses and a browser can no
  longer keep serving last week's views (the "Ctrl+Shift+R after an update" problem). Those addresses
  are cached for a year, which also makes the app faster to open.
- **A Content-Security-Policy** on every page: scripts only from this server, no inline code, no
  objects, forms only to this server. A stored script in a name now has nowhere to run. The two inline
  handlers that existed were rewritten. The Node-RED editor keeps its own policy.
- **The API relay refuses this machine and its neighbours** — loopback, link-local (the cloud metadata
  address), and the company's private ranges unless `CBX_API_ALLOW_PRIVATE=true` — at save time and
  again at call time, by resolved address, so a workflow cannot reach Ollama or the Node-RED admin API
  through a "connection".
- **Cookies are Secure over HTTPS** on their own: any request that arrived over HTTPS (the tunnel) sets
  a Secure cookie; the LAN's plain HTTP still works. `CBX_SECURE_COOKIES=true` forces it everywhere.
- **Device cookies are stored hashed**, like the sign-in codes (a migration hashes the existing rows).
- **Two uploads of the same name at the same moment are two files**, not one overwriting the other
  (the name is claimed atomically).
- **Two people pressing Send at once no longer share a request number** (a collision is retried), and
  **only one admin wins Approve or Decline** — the other is told to reload.
- **A version's tests, a PDF's OCR and a reminder's email no longer hold the database's write lock**
  while they run; the pulse skips a tick quietly instead of logging a trace when the database is busy;
  the grid's one-cell-per-company-and-type rule is now an index on databases created before it existed.
- **The pulse watches the mailbox**: mail that is on but failing is an alert within two minutes,
  because sign-in codes are skipped (on purpose, loudly) while mail cannot go out.
- **A git snapshot of the live tree at every promote and rollback** (`data/` and `.env` never in it),
  when git is installed on the server — history to read and recover from, next to the backups.
- `/forgot` is rate-limited like the sign-in door. A migration step no longer commits mid-migration.

Not done from that audit, and why: temporary passwords still do not expire on their own (the owner
sees "reset requested" and reissues; an expiry is a small follow-up); the sandbox is still a path jail,
not a privilege jail (IT list); `restart.bat`, the `nul` file and the `*.ngrok-free.dev` wildcard are
on the server, not in the package — see the notes that came with this release.

### One app: the top-down AI company and CBX-Connect 2's look (1.0.114)

CBX 1 is now the one app; CBX-Connect 2's best parts move into it release by release. **Top-down AI:** a goal (from the
owner, an admin, a team's schedule or a problem the heartbeat spots) is planned by C-EXA into parts for team leads; leads
assign items to their agents and hand work to other teams; questions go agent → lead → C-EXA, who answers from earlier
decisions or decides herself; the owner sees only approval taps and goals still stuck after three tries (Agents → Now:
goals with progress, Waiting for you, Done today). Each goal has a call budget, at most 25 pieces of work and 48 hours;
work that read mail or the web stays read-only all the way down; problem and schedule goals act as C-EXA's limited
account, and C-EXA's account is no longer an owner. **Look:** all ten CBX-Connect 2 themes, the menu on the side or along
the top, larger text — per person in Settings (default unchanged: Classic, side menu); Home reads top to bottom (Needs you,
Ask, Today, Goals in progress, Happening now, Tools; users: Your work, Your tools, Your progress); tool colours.
**Versions:** an uploaded version's tests no longer receive the server's settings and secrets (they still run under the
server's Windows account — only upload versions you trust); Promote backs up the databases too, and Roll back can put the
data back (people stay disabled/locked, everyone signs in again, the audit trail is kept).

### Sign-in: authenticator codes work with a drifting server clock; email codes are always checked (1.0.113)

The CBX PC's clock runs about a minute fast, so app codes only passed in the first seconds after they appeared, and many
people gave up and chose "Use email codes instead" — which switched them without ever sending or checking a code. Now:
codes are accepted within ±2 steps of each person's learned clock difference (learnt at set-up, which searches ±5 minutes,
and on every sign-in), and a code can never be used twice; owner/admins see a line on Home when the server's clock is off
(with how to sync it); choosing email codes sends a code that must be typed in, and the 25 accounts that switched without one
are asked for an emailed code at their next sign-in from any browser. Security review fixes in the same release: the
second step now requires a password-passed ticket from the same browser (before, an e-mail address plus one current app
or recovery code was enough), emailed codes are tied to their purpose, the set-up search only runs during a set-up, and
the set-up / recovery / remove steps are rate-limited and count wrong codes.

### The assign dropdown is back (1.0.112)

Owner and admins assign from a dropdown in the ticket's Details card again, grouped as AXL suggests (with the why), works
<system>, and everyone else on the desk (admins marked); AXL's first pick is preselected, a filter box appears for long
lists, and only the Assign / Reassign button assigns — picking a name never mails anyone. Clicking a name in "AXL suggests"
selects it in the dropdown. The test run now removes its throwaway data folder (about 1 GB) when it ends — each "Upload &
test" used to leave one in the server's temp folder.

### The support desk picks the right people; a calmer ticket page; multi-select filters; T-0087's answer (1.0.111)

Suggestions only name people who work the ticket's system: Support set-up has a new "Who works each system" card where
anyone on the desk (support leads and Support-desk people) can be set per system, with "Use AXL's guess" from past tickets.
Owners and admins are never suggested unless set for that system; "handled N tickets" counts only tickets the person was
given and finished themselves; auto-assign only picks someone set for the system. The ticket's Details card is one quiet
card: a status line saying what happens next, People (assigned, accepted, responsible — click to change, AXL's
suggestions as one line), Ticket (system, area, requester, opened, SLA/follow-up), one main button and "More". The
ticket list's status, person and system filters are multi-select (any number ticked; kept in the address). Mail flow:
a reply on an open ticket is always a reply (the new-problem check only runs on finished tickets) — a one-time repair puts
answers that were wrongly held aside back on their ticket (T-0087's invoice number), without mailing anyone; admins get
"It belongs here" on a new-problem note; an empty reply changes nothing; C-EXA never re-sends the same question within 24
hours or two question mails within 30 minutes, and doesn't ask "where" once the system is known; a colleague replying in
the requester's thread (same company, a real reply link) joins the ticket instead of opening a new one; a ticket never
sits in "new" more than 10 minutes and shows since when it is being read.

### The install check passes on the CBX PC; Seth reads invoices again (1.0.110)

1.0.108 and 1.0.109 stopped in the Versions test step on the CBX PC: its Node is older than the `--experimental-default-type`
option the new script checks used ("bad option"). The checks now work on any Node (a script is checked as a .mjs copy; a
snippet with --input-type=module), and skip where there is none; tested with Node 16, Node 22 and no Node. Seth, the senior
invoice reader, was sent to C-EXA's text model, which cannot see the page pictures: every call failed (96 of 96 on 19 Oct).
He now uses the eyes model, like the other readers. Everything in 1.0.108 and 1.0.109 is in this version.

### The support desk: screenshots, removing tickets, filters, pictures in replies, Ask AXL, follow-ups, previews, suggestions, quick find (1.0.109)

Pasted screenshots reach the ticket (only clear logos are left out, and admins see what was); owner/admins can remove a
ticket (tell the requester or silently) and restore it; robot mails never open tickets or get mail; mails join a ticket
only by its tag or their own reply links, never by subject, and a new problem replied into an old thread is asked to come
in as a new e-mail; filters by person, status (Not accepted yet, Waiting on the requester, Escalated), system and age,
grouped by person or status; replies with pictures in place, like Teams; Ask AXL inside the ticket (drafts and actions only
on a person's press); a reminder after 2 working days and escalation to the ticket's responsible person; previews of
pictures, PDFs, text and mails; assignee suggestions that learn, auto-assign per system (off by default); quick find by
ticket number or words; a calmer conversation layout.

### Site Schedules: T100/T000 references fixed, no empty headings in clause 7 (1.0.108)

The reference lines always read T100/<number> and T000/<number> (fixed prefixes, number-only boxes, old values tidied);
clause 7's sub-headings appear only when they have text; the file name's lease code follows the lease type; the 01 April
first-escalation rule now also applies in the Site Schedules folder build.

### Site Schedules: the Swiftnet team's test feedback (1.0.107)

First escalation 01 April of the same year for leases starting 01 Jan–31 Mar, with the 4.1–4.3 values in bold; standby
electricity at R0.00 reads "0 Amp" / "N/A"; Swiftnet signs at Pretoria by Eddy Kgomongwe (CCO), Gert Vorster (CFO) and Paul
Jansen Van Rensburg (CEO), filled in automatically (admins change them under Template); Latitude, Longitude, Site PID and
Region come through from quotes with odd spacing; scope of approved work from the SOW (now accepted), then the NTP; wrapped
charge descriptions stay with their own line.

### IFRS 16 reads the real exports; the support desk finished; a sweep of every tool (1.0.106)

- **IFRS 16:** the RR06 contract register (Contract Movement tab with a merged banner, text numbers, trailing spaces),
  Sequentra's Record Info export with its title block, the PIW Step Payments report and LA's Portfolio Transactions .xls now
  read. 1.0.105 stopped with "No movement sheet found" on these; the September month now gives PIW, MDIW, CIW and
  Terminations, and LA vs RR06 uses the current charges and the lease term in years.
- **Support desk:** a taken ticket's mail no longer offers Accept; staff see their own tickets first, then the free ones by
  age, with a "Free to take" filter; losing a Take it race says who took it; a reply to a ticket closed as not relevant
  comes back as a free ticket.
- **Every tool checked in the browser:** a viewer can no longer add rows or press rule buttons in To-do and tool folders;
  Site Schedules shows full amounts; clearer messages when a required pick list is empty; several viewer pages tidied.

### Make SAP upload: one screen, one click (1.0.105)

The AP Tool opens on **Make SAP upload**: drop the invoices (PDFs or a zip), press the button, download the SAP upload.
No folders, tabs or Needs you — those sit behind "More" for admins and reviewers. The AI fills every field itself:
company (the group's own Excellerate Property Services and Nervada → Z999, Excellerate Holdings → Z102 are now in the
company list; a guess can no longer put them on X143), vendor (by VAT number, then the one opened in the company), G/L,
cost centre and tax code (the vendor's coding, then SAP posting history, then the AI's pick from the chart of accounts and
the company's cost centres), due date and invoice date. Every guess is filled in and shown as a yellow cell with a note;
nothing stops for it. Only real problems are left out of the file and listed in one line each: already posted in SAP,
blocked vendor, a VAT decision (Claim / Don't claim right there), or waiting for the second person's check (Valuence),
which the reviewer does from the same screen ("Check it"); "Download again" adds them once settled. Fix yellow cells in
Excel and drop the file back: CBX learns those codings for next time. Portfolios have a new setting "Review every yellow"
(on). Invoices stuck in Needs you go back through the bots by themselves at the first start.

### The Valuence AP controls (1.0.104)

Everything Valuence found in the Egypt, Shell and Telkom AP reviews that a tool can close. **Portfolios** (EFM-SA,
GENSEC, SHELL, TELKOM, SWIFTNET, EGYPT; AP Settings → Portfolios) carry each portfolio's rules (review threshold, sample,
variance %, Shell's SA101 / T100…). **AP people** (Settings → AP people) gives capturer, reviewer, poster, approver and
reconciliation-reviewer roles per portfolio; a reviewer or approver is never someone who dropped, typed, coded or cleared
the document (admins included). Until a portfolio has roles everyone with the AP Tool holds the first four, but the
separation of duties applies from day one. A new **Controls** tab holds the registers:
- **Review** — every document of R50 000 or more, a 10 % sample, anything a person typed or corrected, every yellow, a
  non-standard tax code and a new vendor's first invoice wait for an independent reviewer (Agree / Correct / Reject);
  the SAP upload is refused while any wait. A document changed after its review waits again.
- **VAT** — an invoice that isn't a valid tax invoice (s20: the words, supplier VAT number, recipient details over R5 000…)
  is red until a reviewer picks Claim or Don't claim with a reason; Don't claim posts the VAT into the expense lines.
- **Evidence** — every SAP upload and review workbook is kept as downloaded (SHA-256), evidence can be attached to a folder,
  payment run or account (incl. .msg/.eml), a checklist per portfolio shows what's missing, a monthly zip with an index,
  and nothing is deleted inside its retention period.
- **New vendors** — vendors SAP created or changed after 19 Oct (from the LFA1 upload's ERDAT/ERNAM/AEDAT) are reviewed
  against the request and bank letter by someone other than their creator; their invoices are red until then.
- **Duplicates** — every possible duplicate is a case (genuine never posts; valid needs a reason, and a second person from
  R50 000), with ageing, a near-duplicate rule for utility bills, and an import of the MOL / Workflow listing.
- **Variances & disputes** — each utility bill is compared with the account's own history (amount, kWh, rate, estimated
  readings running, period gaps, check-meter kWh) and the Tariffs table; Accept needs a reason, Dispute holds the bill.
- **Estimates** — missing bills get a proposed estimate (basis per portfolio/municipality), a second person approves it,
  and the real bill trues it up (reversal or next-month adjustment); escalation after 3 running.
- **Completeness** — account status, CO/DO, cluster, check meter and bill day; masterlist / termination / anomaly imports
  with exceptions; each account per month is posted, read, estimated or missing; a Council Liaison list; a bill on a
  terminated account is red.
- **Reconciliations** — a monthly population by rules, a municipal roll-forward from bill history and SAP payments, items
  with owners and dates, preparer and an independent reviewer.

### SAP's vendor list and posting history built in; support staff take free tickets; which bot is on which document (1.0.103)

SAP's vendor list (the Supplier Master: 32 350 vendors, 723 blocked) is built in, so Mia finds a supplier's vendor number
by VAT from the start; several vendors on one VAT number → the one opened in the posting company, then the one with
posting history there, else a picker in Needs you. AP Settings → Posting history reads SAP's FBL1N and FBL3N exports (the
EFM Aug–Sep ones are built in): an invoice already posted in SAP is red; a vendor with no coding whose history mostly uses
one G/L and cost centre is coded from it, yellow "coded from SAP history — check it"; otherwise Code this vendor starts
from the most used coding and shows the last invoices posted. The EFM cost centres of 19 Oct are in. The batch reader
builds lines from where words sit, never takes a label as the supplier or the total as the VAT (the 467332 invoice), and
sends a PDF whose text doesn't add up to the picture readers. Support desk: staff see their own tickets and the free ones
only (lists, counts, links, the Mailer), Take it makes a free ticket theirs, only the person it is with (or an admin)
finishes it, Give back frees it; admins can close any open ticket as Not relevant without assigning it (optional reason
and mail to the requester; never counted as solved). Every file in the AP lists, Needs you, The team and the Office shows
a live chip "● Ivy is reading — 0:42" while a bot works on it; the desk shows its readers the same; a bot with no sign of
life for 3 minutes turns amber and its document goes back to the queue once.

### SAP rows for manual invoices: posting date, cost centre per site, G/L per line, due date, SAP's vendor list (1.0.102)

The SAP rows post on the batch's posting date — today unless someone sets another in the date box beside Download SAP
upload ("Today" puts it back; a batch left at today posts on the day it is downloaded; a date far from today is yellow).
AP Settings → Sites maps the site code after " - " in a bill-to (RIO2X21, DELL) to its cost centre; a site with no row
shows in Needs you → Sites to map. A vendor coding can have line rules (words in a line → G/L, cost centre, tax code) and
payment days (an invoice without a due date is due that many days after its date); a new Fixed value TaxCodeExempt is
used by a rule that marks a line exempt. AP Settings → Vendors (SAP general data) loads SAP's LFA1 export (technical names
or English labels; the columns read are shown first) or pulls it from SAP: Mia finds an uncoded supplier's vendor number
by its VAT number and Code this vendor starts from it; a vendor SAP has blocked is red. The header text fits SAP's 25
characters with the useful part first ("Excellerate Serv. AUG 26").

### Utility bills: the utilities list is built in; Other offers the right choices (1.0.101)

The Harvester's utilities list (10 345 accounts) is built into the AP Tool like the other SAP lists, so Mia finds each
bill's vendor, cost centre and site. When the list changes, bills waiting in Other go back to Mia by themselves. For a bill
PDF Studio can read but whose account isn't listed, Other offers "Add to the utilities list" (admins) instead of "New
profile". The capture readers and the support desk's readers now go before C-EXA's background team work on the GPU.

### The tests run on the Windows server (1.0.100)

1.0.99 stopped at the Versions page's test step on the CBX PC with "UnicodeDecodeError": a test read the AP page's
script as cp1252, Windows' default, and the new page has characters cp1252 doesn't have. Every text file CBX and its
tests read or write now says utf-8 (about 80 places, most in tests; one in the app: the leaked-password list), and a
test keeps it that way. Nothing else changes: everything in 1.0.99 and 1.0.98 below is in 1.0.100.

### Invoices wait while the AI is off; the SAP download looks like the Harvester's; Entity Compliance export (1.0.99)

When the AI is off, the AP capture readers keep invoices in their queue ("Waiting for the AI") and read them by
themselves once it is on, instead of sending them to people; any already parked that way go back to the queue once.
Needs you refreshes while the team works and each row shows its batch and how long it has waited. "Download SAP upload"
looks like the Harvester's grid (green header with filters, the vendor line shaded, Tess's colour on its TransCode cell,
codes as text with leading zeros); same sheet, columns and values, so SAP takes it the same way. Entity Compliance has
an Export button: one workbook with an overview of every entity and its state, then the documents, what is expiring or
expired, the entities and the document types — only the companies you may see.

### The AP Tool reads invoices to the SAP upload; the Support desk accepts, reminds, learns and sees (1.0.98)

Brought over from CBX-Connect 2.10.3, so the old CBX has the same until it is replaced on 28 Oct.

**AP Tool.** Drop one folder of municipal, Eskom and Telkom bills and supplier invoices mixed. Sol sorts; Uma reads
utility bills with PDF Studio's profiles; Cara, Dex and Eli read supplier invoices with the eyes model, one at a time;
Seth (C-EXA's model) takes what they can't read, then a person. Mia matches the company code, the utilities account and
the vendor; Pip builds the SAP upload in the Harvester's FB01/ZB01 layout; Tess tests every document before it can be
downloaded (balances to the cent, codes exist, nothing goes out twice; a vendor not opened in the company code, a cost
centre not in it, or a document type that can't carry vendor lines is red; a G/L not in the chart of accounts is yellow).
Needs you: other entities, odd files, invoices nobody could read, vendors to code, red documents. The team tab shows
the line live. Settings: entities, utilities accounts, vendors, SAP values, document types (SAP's 75; DR, YR and YG
marked Check), G/L accounts, cost centres, vendors per company code, models — from Excel or SAP. The bots show in the
Office as "Sol (AP)" and so on. Ivy, Bo, Rae, Quinn and Paula work as before.

**Support desk.** SAP is a ticket system (and Vendor portal on installs that missed it); both are recognised from what
people write. The lead's mail has **Accept** instead of "My to-do": accepting starts the SLA and opens their To-do.
Reminders: not accepted after 6 hours to the lead, 12 hours to the person set in Support settings (Sashen), 2 days to
both; open over 48 hours in the lead's digest. Each support person sets their digest (daily, every few days, weekly) and
can be **Invisible** (the requester reads "the responsible person"). Mo, Lu and Kit read the mail, Ada checks and
writes the summary, C-EXA only when Ada can't settle something. Iris reads screenshots, photos and scanned PDFs; on an
SAP ticket the lead's summary carries an SAP block (transaction, company code, document and type, G/L and cost centre
with names, vendor, amount, SAP's message). The desk learns what leads had to ask, and where tickets go when someone
changes the system or reassigns (the lead is pre-selected; a person assigns). Tickets open before 1.0.98 are not
mailed about at once: the reminder clock starts at this version, and tickets already being worked count as accepted.

### Sign in with Microsoft (1.0.97)

Company staff can sign in with their Microsoft work account and the company's MFA; their CBX password still works.
Only people who already have an active CBX account under that email get in, and the Microsoft account is linked at
the first sign-in. Outside people keep email, password and the CBX second step. The owner fills in the tenant id,
client id and client secret from IT's Entra app registration under Settings → Microsoft sign-in (the secret is
sealed, never shown again), with a Test button and the redirect address to give IT. The button shows on the sign-in
page once it is switched on and CBX runs on an https:// company address.

### Add user fixed; Support desk (1.0.96)

- **Add user works again.** Since 1.0.93 the welcome mail tick was sent as if it were a tool, and the account was
  refused with "Input should be a valid integer".
- **Support desk**: a new tick under Tools on Add user (and on Tool access) for the people who work support. They
  get the Support menu, see every ticket, write to the requester, close tickets, work the support mailboxes in the
  Mailer, and can be given a ticket. Assigning and Support set-up stay with the owner and admins.

### The AP Tool (1.0.95)

A new tool, **AP Tool** (the same page as CBX-Connect 2.8.6): drop a folder (or a zip, or files) and the AP team reads
every file, says what it is, hands it to its bot and checks it against the AP harness from the SOP review. **Ivy**:
invoices and credit notes (valid tax invoice, VAT at the set rate, totals, dates, order number, duplicates, paid
already). **Bo**: payment runs (duplicates, zero lines, paid before, late, new suppliers, bank account changed, big
change against the last run) and bank letters (call back and a second person). **Rae**: ages the vendor ledger and
reconciles supplier statements to it. **Quinn** (new on the team): logs supplier and internal queries, finds the
invoice or payment and drafts the answer; a person sends it.

What a bot can't place goes up the chain: **Paula** (the quick model reads it) → **C-EXA** (suggests; a person
confirms) → **Needs you**. People fix a file and can **teach the team** ("files with this text are this kind").
Payment runs and bank details: one person checks, a second, different person approves. What the team knows about
suppliers (bank accounts, last run totals, paid items) is learned only from files two people signed off. Each
folder gets an Excel report; the owner and admins set the thresholds.

### The AP team (1.0.94)

C-EXA has a new team, **AP**: the four SSC job descriptions and the AP managers' KPAs in four bots — Paula (lead:
KPIs, deadlines, reports, raising the alarm), Ivy (invoices), Bo (payments and banking) and Rae (reconciliations).
Their rules (the four-eye principle first: bots prepare and check, people approve and pay) and KPIs are the team's
standing order. Give them a job from the team page; they start their own work once an AP tool exists.

### Welcome mail; IFRS 16 as the desktop (1.0.93)

- **Welcome mail**: a new person gets the welcome mail — where to go, their email and temporary password, set their
  own at once. Someone given Entity Compliance also reads which entities they are the responsible person for. A
  tick on Add a user (on by default); the Mailer can hold it as a draft to check first ("Welcome mails to new
  people"). The Sent list never shows the password.
- **Ledger check as the desktop's Report**: lease names, dates and entities from a kept upload and the Sequentra
  validation export; leases the last run ruled out left out; periods (full lease life, from the as-at month, months
  back); the entity on the Recon tab; the desktop's full Consolidated Report and the **simple report** beside it.
- **Master report**: the month and (with a ledger export) the ledger check in one workbook with a Contents page.
- **Sequentra test**: tests the key on a real report, as the desktop does.
- **The .xls error** ("Import xlrd failed"): xlrd and python-calamine are now in requirements.txt (installed on promote);
  the engine also reads an .xls with calamine, and an .xls that is really a web page or Excel 2003 XML. A failed
  upload has **Read again** — press it after the update to read the two failed uploads again.
- **AI check**: when a step fails the app checks it first — fixes what it safely can (installs a missing reader library
  and tries again) or says in plain words what to do; anything else goes to the quick model on the AI box. The words
  show under the error, and in the Audit trail for errors the app notices about itself.

### IFRS 16: the CBX-Connect 2 page, and finance's October fixes (1.0.92)

IFRS 16 is its own page again, the same as CBX-Connect 2 (no more tables and Edit boxes): Monthly workflow (keep
the source files, work out a month), Ledger check, LA vs RR06, Sequentra and Rates. The tool folder's uploads,
months, ledger checks and rates were copied to the page once; the folder stays, out of the menu.

- **Dates** on the PIW and MDIW are real dates in the PIW template's own format (m/d/yyyy), not text.
- **Modification date** is the 1st of the month.
- **True-up adjustments** are left off the PIW (additions); renewals on the MDIW keep them.
- **Funders** on the PIW and CIW have no special characters ("&" becomes "and").
- **Ledger check report**: discount rates from the Sequentra validation report; the new Net Asset columns
  (opening, additions, adjustment, termination, depreciation, closing) after the lease liability closing balance;
  the net asset check shows on the check.
- **LA vs RR06** (ABSA Bank Limited, South Africa): the summary by category, a drill-down per category and the
  reconciliation as its own Excel file.

### Site Schedules: who signs on the right, as the real agreement (1.0.91)

In the Word agreement (and the page beside the form) the witnesses are on the left and the people who sign on the
right, side by side, as the real Site Schedule Agreement: for each, the line to sign on (or the drawn signature),
the name, the title and "For Swiftnet duly authorized" (or the co-user's). (1.0.90 had put them on the left.)

### Site Schedules: the CBX-Connect 2 page (1.0.89)

Site Schedules is its own page again, the same as CBX-Connect 2 (no more table and Edit box):

- **Agreements** on the left: drop every PDF for the site at once (quotes, the NTP, an earlier agreement). The
  list shows each site, its co-user, lease and start, whether the Word file is made, **Download** and how many
  times it was taken.
- **One agreement**: the fields in sections on the left, the charge tables with every comment its own line (add,
  change or remove lines; extra tables), who signs; on the right the Word page exactly as it will print. It saves
  as you type. **Make the Word file** downloads it; **Download Word** takes it again; **Add PDFs / read again**.
- **History** on each agreement: who added and read which PDFs, what was changed, every Word file made and every
  download, with the time.
- **Template** (admins change it; every save is a version you can go back to) and **People who sign** (with a
  drawn signature that goes on the agreement for that name).
- Site Schedules can now be given to anyone (People & access), not only admins.
- The rows made in 1.0.88's table are copied to the page once, at the first start (their PDFs read again, the
  fields typed on the row kept). The old table stays, out of the menu.

### Site Schedules: CBX-Connect 2's reading and Word agreement (1.0.88)

Site Schedules reads Swiftnet PDFs the way CBX-Connect 2 does, so the team can test it here before go-live:

- **Drop the quotation, and the NTP or an earlier signed agreement if you have one**, and press **Read it**. Every
  numbered comment on the quote is its own charge line (6.1 rental, 6.2 electricity, 6.3 escort and road, 6.4 once-off).
- The row has every field of the agreement (site, co-user, Co-locate ID, contract, T100/T000, dates and term, mast,
  heights, scope). Open the row (**Edit**) to fill in or correct any of them, and to add who signs (one per line,
  "Name, Title"). What the PDFs did not hold is a line under *What to check*.
- **Make the agreement**: the Word document is laid out like the real Site Schedule Agreement (no lines under the
  headings) and named like the real files (`9130 - 5183 - 70204797 - SITE - 01.08.2026 - 31.07.2031 - 7708 - R - VDC.docx`).
- Added the NTP later? **Read it again**. Older rows keep their site; press **Read it again** to read them the new way.
- The same engine as CBX-Connect 2 (`cbx/schedules/reader.py`, `template.py`), so both make the same document.
  A Site Schedules draft in the Builder (if one is open) is older than this: throw it away, or it replaces this one when published.

### The Builder never goes down for one bad draft; C-EXA works longer (1.0.87)

- **Builder fixed.** It showed "Something went wrong on our side": the Site Schedules draft had its Quotes
  table saved as `{"columns": {"province": …}}` — someone (C-EXA, through the Builder) meant to add one
  Province pick-list and replaced the whole table with it. Checking that shape crashed, and with it the
  Builder. Now the Builder always opens, the draft shows "columns must be a list …" under *To fix before
  testing*, and **Throw away this draft** takes you back to the tool as it runs.
- **It cannot happen again.** A table of the wrong shape is refused ("Nothing was saved"). A table change from
  C-EXA is laid over the table: `{"columns": {"province": {…}}}` adds or changes only those columns and keeps
  the rest; "Gauteng, Western Cape" becomes a proper list and "pick-list" a choice column.
- **More steps.** A team agent may take 30 steps on a piece of work (was 12) before it asks up; C-EXA takes 60,
  and when she runs out she carries on by herself up to 3 more rounds before she asks you
  (`CBX_AGENT_MAX_STEPS`, `CBX_CEXA_MAX_STEPS`, `CBX_CEXA_ROUNDS`). A long run always keeps what it was
  asked to do in front of it.

### Sequentra on the office network (1.0.86)

On the office network `api.excellerate.tech` resolves to a private 10.x address, so CBX refused it as "on the
company's own network" — the Sequentra connection for IFRS 16 could not be saved, and two IFRS 16 tests failed
on the server. Named company hosts may now be on the private network without opening all of it:
`CBX_API_PRIVATE_HOSTS` (default `api.excellerate.tech`, comma-separated). Every other private address is still
refused unless `CBX_API_ALLOW_PRIVATE` is on, and this machine and link-local addresses never are.

### The report chain — the teams ask up, not the owner (1.0.85)

- **Questions go up one level.** A helper, operator or developer that is stuck or unsure uses `ask_up`: its
  team lead answers. A lead asks C-EXA (the big model), who reads the code, the history and the data and
  answers. Only C-EXA asks the owner (`ask_owner`) — when the choice is truly a person's (rights, access, money,
  deleting, going live, mail out, someone's job) or she is still unsure after looking — and she leads with her
  recommendation. The answer comes back down the chain and the work carries on; "Stop…" from above stops it.
- **Also up the chain, not to the owner:** an agent that goes round in circles or runs out of steps, and a
  result it is less than 50% sure of (the lead checks it: "Yes, that's right" makes it done, "(Checked by …)").
  If the one above cannot answer, or a piece of work has asked up three times, it comes to the owner.
- **Unchanged:** what is the owner's tap by rule (rights, access, disabling, deleting, promote, mail out) still
  waits in Approvals — that is safety, not a question.
- **One line a day.** After 17:00 (`CBX_DIGEST_HOUR`) C-EXA posts "The teams today": questions answered inside
  the teams (and by whom), work finished, what did not work out, what waits for you.
- **One GPU gate.** Every model call — agents, support reading, heartbeat, mail agents, C-EXA's chat — passes one
  gate: `CBX_GPU_SLOTS` at a time (default 1), and a person waiting (chat, voice, a call) goes before background
  work. Many agents no longer land on the card at once. With a big model for C-EXA and a small one for the
  teams, Ollama keeps both loaded with `OLLAMA_MAX_LOADED_MODELS=2` and `OLLAMA_NUM_PARALLEL=1`.
- **Entity Compliance grid on a wide screen.** A page with a grid uses the whole width, the document-type
  columns are narrower, and the grid scrolls inside the screen with its header row and company column kept in
  place — no document type is hidden off the right edge. Names in tables are no longer underlined, and the tab
  row no longer shows a tiny up/down scrollbar on Windows.

### Support mail as it really arrives — the Chase mailbox (1.0.84)

Checked against 38 real mails from the Chase support mailbox (read in the session only, then deleted —
nothing from them is in CBX). What C-EXA's support reading now does with mail like that:

- **Forwards and "see below".** "Kindly assist with the below" brings the earlier messages of the chain onto
  the ticket, each headed with who wrote it and when, so the request four messages down is what the team
  reads — not "Kindly assist". External-sender banners, disclaimers, tracking links and signature phone
  numbers are taken out; details written in the body (the e-mail of someone who needs a login) stay.
- **Reminders, chasers and doubles.** A reminder on a ticket the team already has stays on that ticket (the
  lead, or the people who assign, are told "X is waiting on it — reminder 2"); a colleague chasing on the same
  chain becomes a note on the ticket, never a second ticket and never mailed; the same request sent twice
  lands on the first ticket; a reminder is never taken as the answer to a question.
- **Thank-yous.** A thank-you with no ticket is not a ticket (Mailer shows "Skipped — only a thank-you"); one on
  a closed ticket is kept and does not reopen it.
- **Passwords.** A password, PIN, one-time code, card, bank or ID number in a mail (a forwarded Chase welcome
  mail, a meeting invite) is removed before the mail is kept, read by the model or relayed; the ticket gets a
  staff note that something was removed.
- **Chase's problem areas** now match what people ask: Signing in / password reset · Access for a new person ·
  Sites, countries or company codes I can't see · Approvers and user rights · Loading or fixing a site or client
  profile · Quotes, POs and line items · Work orders / job cards · Invoices / payments · Reports · Training ·
  Something else. Added once to an existing install; areas changed or made in Support set-up are kept.
- **Where and what.** Countries, sites and PIDs named in the mail become "where"; signing in, access, rights,
  reports and training are not asked "which site?"; the sentence that asks for something becomes "what they
  need done". On a mail, what the model read in the whole chain wins over these rule guesses, and it may say a
  mail is **not for support** (colleagues talking, a billing matter) — then nobody is mailed and the owner and
  admins see it as "probably not for support".
- **Attachments.** Signature logos and pasted pictures are no longer saved as files, so the spreadsheet that
  matters is never pushed past the limit; an attached mail is kept as a .msg; the same file on a reminder is
  kept once.

### One look, no cut-offs, one button per job (1.0.83)

Every screen was opened in a real browser at desktop and phone width and checked by eye and by script
(62 findings); all of them are fixed:

- **One way to do each thing.** One close button per dialog; one "Customise" on Home; one "Add user"
  (pick the role); "New team" means a department, "New agent team" means C-EXA; "Give work" everywhere;
  the old Doc Watch page now opens the Entity Compliance tool; Approvals holds C-EXA's asks too;
  Results moved into PDF Studio; the Sandbox/Playground cards left Versions (Work areas has them);
  the Developer page lost its second Talk button and the board its second "Add a task".
- **One look.** Page tabs are underlined and sit above the title — no tabs inside tabs (People has
  People · Tool access · Roles & permissions · Row-level rules · Org chart; C-EXA has Talk · Now ·
  Teams · Jobs · Standing agents · Developer). Row buttons are outlined, delete ones outlined red; one
  link colour; file fields are drop areas everywhere; search boxes match the other inputs.
- **Nothing cut off.** Long titles end at a word with "…"; dialogs keep their title and buttons on
  screen and scroll in the middle; table headers stay on one line; the org chart, the team chart and
  the office wrap onto rows and fit the page; the Builder rule editor is readable again.
- **Plain words.** No setting names or `.env` on screen; "items", not "rows"; the audit trail shows
  names and words ("Tool item added"), not codes; menu names: My tickets · My requests · All tickets ·
  Support set-up; Mailer → Mail set-up; C-EXA is "her" everywhere; sample mails use made-up names.
- **Says why.** The Mailer opens on Drafts, hides Send (and Send all) while mail out is off and says
  why; Profiles & models shows "—" when nothing was read and explains the spend; IFRS 16 month boxes
  show "e.g. 2026-08"; the Sequentra key steps are for admins only.
- **Settings.** My settings · Branding · C-EXA voice & models (moved out of C-EXA → Now) · API
  connections · Data warehouse.
- **Phone.** A Menu button opens the menu; Ask C-EXA sits in the top bar; no page scrolls sideways —
  wide tables scroll inside their card.

Dates show in the PC's own format (a browser on US English shows mm/dd/yyyy).

### Save → Try → Test → Deploy, more teams, and C-EXA builds (1.0.82)

**The Builder's buttons are the four steps, in order** (top right): **1 Save** keeps my changes as a
draft · **2 Try** uses it as people will — here in the page (buttons run dry) or in my own Dev area ·
**3 Test** packages my drafts as the next version and runs every test; the result is right there:
passed / failed, the failing tests, and *See the test* for the whole run · **4 Deploy** puts a passed
version in **Test** for others to try, then **Go live** (the owner; ten minutes' notice). Each step
says where the tool is ("Saved ✓", "Passed · v1.0.83", "In Test"); a change after the test asks for a
new one. Developers (not only admins) have the Builder.

**More teams of agents.** C-EXA → Jobs & teams → **New team**: a name, what it is for, the kind — a
*working* team does the work in the app with its hands (people, files, rows, tools), or one that
builds, changes or advises — and, if wanted, a schedule (on its own, a working team gives its lead the
standing order). It starts with a lead, an operator and a helper; *Give it work* hands the lead a job.
Teams someone made can be removed (not while work is open); the ones that came with CBX are switched
off instead. Every agent has its own name, so work always finds the right one.

**C-EXA builds, redesigns, changes, tests and fixes tools** — the same four steps, through the
Builder's own API as the owner: `tool_folder` (read a tool whole), `build_tool` (new or redesigned),
`change_tool_part` (one page, table, rule, words, the manifest or README), `try_tool` (a page as the
draft draws it, or a rule run dry), `test_tools` (package and run every test — the failures come
back to her to fix) and `deploy_version` (into Test herself; **live waits for your tap**). She can
also `make_team` and `give_team_work`.

Held by the app, not her prompt: she can never answer or approve her own waiting asks (nor a job's
questions or plan, nor a call); removing a team waits for your tap. Fixed on the way: the answer
C-EXA reads no longer hides a Builder column's `key` or a count of tokens as if they were secrets.

### C-EXA runs the app — one owner, one tap (1.0.81)

The goal: one person runs all of CBX. C-EXA and her agents now do the work in every part of the app — people, files,
tickets, tools, PDF Studio, mail drafts, work areas — through the app's own API, as the person they work for.
- **She does routine work herself**: adds a plain user and changes their details, resets a password, makes folders
  and moves or renames files, handles tickets and support, rows of the tools, Builder drafts and releases (tested,
  not live), work areas, PDF Studio and harvest, mail drafts and templates. Every call is in the audit trail as the
  person, "by C-EXA".
- **Risky work waits for your one tap**: admin or owner rights, anyone's access to a tool or a role, disabling
  someone, deleting anything (a file goes to the **Bin**, never gone), promote or roll back, mail that leaves the
  building, approving what she learned, business approvals, requests, settings. She puts it in **C-EXA → Jobs &
  teams → For you** with the exact call it will make; **Approve** runs it as you, **Not now** drops it. The menu
  count shows how many wait (also from your phone's browser).
- **Never hers**: signing in, anyone's password or two-step, viewing as someone, API keys, your own mailbox.
  Passwords and codes in an answer go to you in Messages ("Hand this over") — never into the model.
- **The app holds the line, not her prompt**: the same rules (`cbx/cexa/policy.py`) are checked by the server on
  every call her session makes.
- **Agents have real hands**: leads, operators, the people agent and helpers can now use the app (people, files,
  rows, PDF Studio, any API call) for the owner the work is for — under the same rules. Still never a shell or
  the sandbox; an agent that read a web page loses its changing hands for the rest of that work.
- **New tools for her**: list people, add / change a person, reset a password, give access (tap); find files, move,
  rename, make a folder, put in the Bin (tap). The warehouse got the routes for it (rename, move, new folder, Bin,
  find) — files under Tools/ belong to their tool and are not moved.

### Work areas: Dev → Test → Live (1.0.80)

More people now build and try CBX at once, without touching production. **Admin → Work areas**:
- **My Dev area** (everyone who builds — admins and the *Developer admin* role): **Start** runs the live app,
  with **your** Builder drafts laid in, on a port of its own with its own data (kept between starts; *Empty its
  data* clears it). **Open** takes you there through the sandbox door at the usual address (a brown strip says
  *Dev area*); the sign-in shown is for that area only, new at every start, and shown to you alone.
- **Test** (one, shared): a released version whose tests passed is put there (**Put in Test** on Work areas or
  Versions) and everyone who tests (admins, and anyone with *Run a sandbox* or *Propose code changes*) gets their
  own sign-in to try it.
- **Live**: the owner promotes from Versions. A version that has not been in Test is refused unless the owner
  says *Promote without Test* on purpose (logged).
- **Drafts are personal**: a Builder draft belongs to whoever first changes it — nobody else saves over it, throws
  it away or releases it (the owner may *take it over*, logged). The Builder's tool list says who is changing what;
  Release packages your own drafts.
- **Developers never see production's rows**: a builder who is not an admin gets Builder previews without data
  (their Dev area has their own), and the dry run of a rule (which reads live data) stays admins'.
- A Builder release remembers which version it was made from; once another version has gone live since, promote
  refuses it (it would undo that one) — release it again.
- Sandbox A and B stay what they were (C-EXA's builds and uploaded versions, for admins). Ports: Test 8100, Dev
  areas 8101 upward (`CBX_AREA_PORT_BASE` moves them); like A and B they listen on the server only.

### Clean-up, every API address checked, and IFRS 16 as the desktop 5.16 (1.0.79)

**Clean-up — what went, and why.** Every button, page and API address was checked against what calls it.
- **Gone because nothing reached them any more:** the old built-in To-do, IFRS 16 and Site Schedules pages and their
  APIs (the tool folders replaced them in 1.0.55–1.0.57; the one-time copies into the folders stay), the old Home's
  summary, check-ins and "getting started" list (Home is the tools/home folder), *Settings → Mail* (everything is on
  the **Mailer**: Sent now has **New mail**, and its test mail also switches sign-in codes on), a hidden *My mail*
  address, eleven API addresses with no caller (a chat "mark read", a sheet export, a warehouse preview, a C-EXA
  message box, a second version check, …), the browser relay of API connections, about 30 unused functions, and
  the last Node-RED leftovers. Old links (`#/todo`, `#/ifrs16`, `#/site-schedules`, `#/mail`, `#/mymail`) still land
  on the right page.
- **Fixed:** Work → Develop broke as soon as a card was given to C-EXA; four Help lessons pointed at screens that
  no longer exist (removed; the Entity Compliance ones come back with the Builder lessons); a Home number without a
  link looked clickable.
- **Less on screen:** Settings → Mine lost its three notification switches and "compact tables" (they were saved
  but changed nothing) and one of its two *Change password* buttons; Support, My requests and PDF Studio → Harvest
  each lost a button that repeated the menu or the empty-page button.
- **The API is checked on every release:** a new test calls all ~440 addresses — signed out (every one must refuse;
  only sign-in, branding, the version and mail links answer), as a user and as the owner — and none may break.
- The tool folders' tables (`tools/*/data`) were left out of the source history by a too-wide ignore rule (the
  packages always had them); they are in it now.

**IFRS 16 — the finance team's desktop IFRS 16 Workflow 5.16.0, in the tool.**
- The engine is theirs, copied unmodified: the newer lease engine and ledger check, and its **Sequentra API**
  client. Nothing else in the tool changed for the people using it.
- **Work out a month** now asks where the lease data comes from: **the kept files** (as before) or **the Sequentra
  API** — lease facts straight from Sequentra (Building & Record joined to Key Dates), and an **API Cross-Check**
  of every lease in the month against what Sequentra holds (status, dates, discount rate, landlord, customer, charge
  lines), with its flags added to the Exceptions. The LA register and the RR06 / ARO06 stay files; step payments too,
  until the charge reports are confirmed (exactly as the desktop does).
- Each month now keeps **Working papers** too: every tab of the run in one workbook (Recon Summary, IBR Check,
  Routing, Exceptions, the drafts, the cross-check) — what the desktop shows as its tabs.
- **IFRS 16 → Sequentra API:** *Check* calls each of the ten reports once and says how many rows came back or why
  not; *Download* saves every report for a month (or a range) as CSV in one zip.
- **The key:** an admin adds it once in **Settings → API connections → Add** — name *Sequentra*, address
  `https://api.excellerate.tech/sequentra/`, key sent as a **header** named `Clientkey` (the Power BI key), tick
  **IFRS 16**. Stored sealed, never shown again, never in a row, a file or the audit trail.
- **IFRS 16 → Discount rates:** the client's treasury rates, one row a month (bands A–F). SA leases take their IBR
  from here (the newest month at or before the workflow month); with the page empty the month says so.
- Upload also takes the **LA BU asset report** (fills the termination Asset IDs) and the **CIW master**.
- The Builder learned a **pick-list question** on row buttons (beside a date, a month and a line of words).

### 112 more billers from the October Telkom batches (1.0.78)

The three October 2026 Telkom batches (Batch 1, Batch 2 and the landlord invoices — 1 726 bills in 172 folders)
were run through the profiles, and a profile was made for every biller no profile knew yet: **112 new profiles**,
98 municipalities (Hantam to Polokwane, JB Marks, Walter Sisulu, Saldanha Bay, Witzenberg …) and 14 landlords
and electricity re-sellers (CSIR, SANParks, Midstream, PEC Metering, Wattage Metering …). PDF Studio now knows
**177 profiles** — about 170 billers.

- **1 701 of the 1 726 bills are recognised**, 1 575 with an electricity row. Not recognised: 17 scans of
  Laingsburg and Kamiesberg that hold only a barcode or OCR noise, one empty file, two non-bills (Lephalale), a
  newsletter (Overstrand), one bill with a scrambled font (Stellenbosch), and three single bills of another layout.
- Every profile's ex-VAT electricity total was checked against a figure the bill prints itself (an electricity
  sub-total, the electricity VAT ÷ 0.15 or a service summary) wherever the bill has one — on most to the cent.
- **Sundays River Valley** bills were taken by the King Sabata Dalindyebo profile (same layout); they now go to
  their own profile. Every bill the earlier profiles read still reads the same.
- Points a person should look at are listed in the notes that came with this release (a VAT-inclusive-only
  layout, a line that may or may not be electricity, a probable meter multiplier).

### Municipal profiles tested on the whole Telkom folder (1.0.77)

All 1 197 PDFs in the Telkom folder (1 012 different files) were run through the profiles: **1 008 recognised**,
937 with an electricity row; the other 71 are water / rates / refuse-only accounts, which the profiles read but
PDF Studio's municipal template (electricity) has no columns for yet. Made and fixed on that run:

- **Tshwane e-billing PDFs** ("requires Adobe Reader 8") — 35 bills that looked empty. The bill travels inside
  the file as form data; reading it failed on one detail and is fixed, and a new profile (`cot_xfa`) reads it. On
  every one the electricity lines add up to the bill's own "Totale lopende heffing (B)".
- **George, May–September 2025** — an older statement layout no profile knew (`george_2025`); its amounts print
  glued together (1000.002000.00) and are read as incl − VAT. On all 5 the electricity lines equal the bill's own
  electricity CHARGE row to the cent.
- **Tshwane prepaid TOU** bills printed as normal PDFs: kWh (E_PK_PP / E_OP_PP / E_STD_PP) and the ex-VAT total
  now read — equal to the bill's own "Total Current Levy (B)".
- **Overstrand** bills with Afrikaans headings (REKENING NOMMER …): account, month and site now read.
- **Scanned bills** (Nelson Mandela Bay, Govan Mbeki, Mbombela, Newcastle, Greater Kokstad, Magareng, Swellendam)
  are OCR'd like in the Harvester (Tesseract — `CBX_TESSERACT_CMD`); every value read from a scan goes to a person.
- Afrikaans month names in dates ("Junie 2025") are understood.

### Harvester profile pack v4: 63 municipalities (1.0.76)

The Harvester's profile pack v4 (8 Sep 2026, tested on 487 bills) is in: **45 more municipalities** — Abaqulusi,
Alfred Duma, Bela-Bela, Breede Valley, Buffalo City, Dihlabeng, Ditsobotla, Drakenstein, eDumbe, Emalahleni,
Ga-Segonyana, George (and George Amptera), Govan Mbeki, Greater Kokstad, Joe Gqabi, King Sabata Dalindyebo,
Knysna, Magareng, Matjhabeng, Matlosana, Matzikama, Metsimaholo, Midvaal, Modimolle-Mookgophong, Mogale City,
Mossel Bay, Naledi, Nama Khoi, Nelson Mandela Bay, Newcastle, Ngwathe, Oudtshoorn, Phokwane, Rustenburg, Sol
Plaatje, Stellenbosch, Swartland, Swellendam, Tokologo, Ubuntu, Ulundi, uMgungundlovu, uMhlathuze, uPhongolo —
and the two fingerprint fixes (**Mangaung** no longer catches bills sent from our own mailbox; **Tshwane** no
longer catches Matlosana bills).

- **Comma-decimal money** ("55 473,14") now reads as R55 473.14, not 100× too much — the pack's engine fix,
  applied together with its Ditsobotla profile (it changed nothing on any other bill).
- Account and meter numbers in the profiles' notes were taken out; the rules themselves are unchanged.

### The CBX Harvester's municipal profiles in PDF Studio (1.0.75)

The 18 municipal profiles from CBX Harvester 6 now read bills inside PDF Studio (and in Harvest folders): Centlec,
City of Cape Town (English and Afrikaans), City of Johannesburg, City of Tshwane (and its smart-meter update),
Ekurhuleni, Emfuleni, Eskom, eThekwini, KwaDukuza, Maluti-a-Phofung, Mangaung, Mbombela, Msunduzi, Overstrand,
Steve Tshwete and Ugu.

- **PDF Studio → Municipal bills → Use** gives the Harvester's General template: its 28 columns (Site Name, Billing
  Date, Munic/Eskom, Tariff, Account, Meter, Actual/Estimate, dates, readings, kWh by band, kVA, Rands by band,
  Other, Total ex VAT, Deposit / Cash / Guarantee / Security Held). A template of your own with those columns
  (by name — "Account No", "Total kWh", "Total excl VAT" …) works too.
- A bill a profile recognises is read **by the Harvester's own rules** (the same engine, copied unchanged — checked
  value for value against Harvester 6) — nothing learned or guessed. It shows as read by "<Municipality>
  (Harvester profile)" in PDF Studio's profiles.
- The Harvester's **checks** run on every bill: end reading below the start, negative kWh, Peak + Standard +
  Off-peak not equal to the total, charge lines not adding up to the ex-VAT total. A figure that fails goes to you
  instead of being trusted.
- Bills no profile knows go PDF Studio's usual learning way, in the same batch.
- An updated or new profile: put its `*.profile.json` in `data\pdfstudio\municipal\` on the server — the same file
  name replaces the built-in one.

### Tickets that know everything, the support person's to-do, and Mark as done from the mail (1.0.74)

- **Every ticket must say where the problem is and what must be done.** Where = the place of the problem (a site:
  country, city, building — or the page in a system), not where the person sits: someone in Cairo can report a site
  elsewhere in Egypt. The Ticket request form asks both; for a mailed ticket C-EXA asks until she has them (with the
  system and anything else missing). Both show on the ticket and in the lead's mail.
- **Assigned:** the support person gets one mail with everything — where, what must be done, system, who, the summary —
  and the ticket lands on **their To-do** (To-do → Mine). Handed to someone else: the first person's to-do is
  finished, the new person gets their own.
- **The support person needs more:** they just **reply to the mail**. C-EXA puts it to the requester ("Lebo from the
  support team needs a little more: …") in the requester's own mail chain, and mails the answer back to the support
  person in theirs. A request for a password, PIN, code or bank/ID number is never passed on; a stranger replying with
  the ticket number is not the support person.
- **Every mail to the support person** has three buttons: *Open the ticket*, *My to-do* (CBX-Connect → their to-do)
  and **Mark as done**. That opens a small page (no sign-in — the link is theirs, for that ticket, while it is
  theirs, 30 days): they type what was done and press the button. The ticket closes, the to-do is ticked, the
  requester gets the "done" mail in their chain. Opening the link alone changes nothing (mail scanners open links).
  The link is never passed on to the requester, even if it is quoted in a reply.
- New ticket mail **"The support person needs more"** on Mailer → Ticket mails; the lead's "assigned" mail now
  carries where / what must be done.

### One mail chain per ticket (1.0.73)

Every mail CBX sends a requester is now a **reply on the last mail of that ticket's chain** — theirs, or the one CBX
sent them before — so their mail program shows one conversation with everything underneath:

- a ticket that came in by mail: CBX replies on the requester's own mail, and on its own last reply after that;
- a ticket made in the app: the first mail starts the chain, every later one replies on it;
- the chain keeps its own subject (`RE: Portal broken [T-0012]`); the ticket number is added when missing;
- mail read before 1.0.71 is found again by its Message-ID; a reply with the ticket number taken out of the
  subject still lands on its ticket (same Outlook conversation, same sender);
- CBX marks its mails to the requester (a hidden Outlook property, on its own copy in Sent Items) and only ever
  replies on those or on the requester's own mails — **never on a mail to the lead**, so the team's mails never end
  up in the requester's chain.

### Studio voice set-up fixed (1.0.72)

*Set up the studio voice* stopped at the second step with `[WinError 2] The system cannot find the file specified`
when the data folder is set as a relative path (`CBX_DATA_DIR=data`): the new Python environment was made one folder
too deep and then not found. Its paths are now absolute; the stray copy is cleared on the next try. Press **Set up
the studio voice** again.

### Drafts: check mail before it goes, the mail chain, and the wording of ticket mails (1.0.71)

- **Check before it goes** (Mailer → Settings): tick *Ticket mails*, *Compliance reminders* and/or *Flow mails* and that
  mail waits as a draft instead of going. Ticket mails are ticked when 1.0.71 arrives. Sign-in codes and tests always
  go straight (nobody could sign in otherwise).
- **Mailer → Drafts** (owner and admins): every waiting mail — to whom, its ticket, the message. **Check / change** the
  subject and words, **Send** (or *Save and send*), or **Throw away**; *Send all* when they are all fine. Through
  Outlook the draft is also in **Outlook → Drafts** of the sending mailbox — send or delete it there and CBX notices
  ("handled in Outlook").
- **Mailer → Sent**: every mail CBX tried to send lately, where it really went, and why one did not.
- **The mail chain**: when a ticket came in by mail, CBX answers **in the requester's own thread** (a reply to their
  latest mail, so the whole chain is underneath). For a ticket made in the app, the earlier messages on the ticket
  are added underneath instead. The team's notes never go to the requester.
- **Mailer → Ticket mails**: the wording of each ticket mail (asked for more, ready, picked up, a message, done, and the
  two mails to the lead) with an example as you type; *Back to the original* undoes it. Words in braces
  (`{first_name}`, `{ref}`, `{questions}` …) are filled in per ticket. Mails now go out as a tidy HTML mail: a heading
  with the ticket number, the message, a button to the ticket, and the reply line.
- The empty ", ZA / T: | M:" block and the disclaimer under CBX's mails are added by the company's mail server for the
  CBX-Connect mailbox, not by CBX — IT can fill in that mailbox's details or leave it out of the signature rule.
- `.env`: `CBX_MAIL_DRAFTS=support` is the starting choice (comma list: support, reminder, flow); the Mailer's Settings win.

### The Mailer: every mailbox in one place, and mail settings without .env (1.0.70)

**Workspace → Mailer** (the owner, admins and support leads) replaces *My mail*. On the left:

- **Support mailboxes** — pick one to see its mail of the last day / 7 / 30 days straight from Outlook, and what
  became of every mail: its ticket (click it), an answer on a ticket, *Skipped* and why (an automatic reply, our
  own mailbox, a flood), or *Not read yet*. Open a mail to read it. **Make a ticket** turns a mail the agents
  skipped or missed into a ticket; **Put on a ticket…** adds it to an existing one (as the requester's answer when
  it is theirs, else as a note). **Read now** reads that mailbox at once. Problems (Outlook closed, a mailbox
  renamed) show on the mailbox itself.
- **Mine** — your own mailbox and its three agents (what was My mail). Only you see it.
- **Settings** (owner and admins) — mail on/off; out through Outlook or SMTP; **from which mailbox** (picked from the
  mailboxes in Outlook, so the name always matches); **Send a test**; and the **support mailboxes**: add one from
  Outlook's list, its folder, on/off, and the date to read mail from (switched on without a date: from that moment,
  never the whole history). Saved in CBX — no .env edit, no restart. What is not saved here still comes from .env.

A support lead sees the support mailboxes only — never someone's own mailbox (a mail is only opened through the
Mailer when it sits in a support mailbox).

### My mail: your own mailbox, looked after by three agents (1.0.69)

**Workspace → Mailer → Mine** (the owner; *My mail* until 1.0.70). The launcher lists every mailbox in Outlook on the CBX server (the support
mailbox is marked as taken); press **Link** on yours. Not listed? Add the account to that Outlook (File → Add
account) and press Refresh. One mailbox per person; only the person who linked it sees it.

Three agents look after it, every 3 minutes, starting with the last 7 days:

- **Scanner** — brings new mail in and reads it: a one-line summary, a category (Needs reply, Action, Waiting,
  FYI, Newsletter, Finance, Personal, Junk?), how urgent, whether it needs a reply, and what you have to do.
- **Sorter** — puts a "CBX: <category>" category on the mail in Outlook and flags what needs a reply. It files
  mail into folders **only by rules you approve**: when it sees a pattern (three newsletters from one sender) it
  suggests a rule under **Rules**, and nothing moves until you press Yes. You can add your own rules.
- **Writer** — writes reply drafts into that mailbox's **Drafts** (open them in Outlook, check, send), starting
  from your **Templates** where one fits ({first_name}, {subject}, {my_name} are filled in), adds what you have to
  do to your **To-do**, and offers a template when it keeps writing the same kind of reply.

They never send, delete or forward anything, and never open links or attachments. What a mail says is read as
the sender's words, never as instructions. Mail text is kept only while it is being read; the summary stays.
**Unlink** forgets everything CBX made of the mailbox — the mailbox in Outlook is untouched. Settings: switch
off automatic drafts or to-dos. Outlook must be open on the server (as for the support mailbox).

### Her voices, and a studio voice on the graphics card (1.0.68)

**Pick her voice** (owner): C-EXA → Jobs & teams → Now → Voice → *Her voice*. Press ▶ Hear on any voice,
then Use. Everyone hears the one you pick — in Talk, in the drawer, on calls.

- **Studio — British women** (Charlotte, Emma, Harriet, Imogen): Chatterbox Turbo (MIT licence) on the
  graphics card — fuller, more natural speech. Press **Set up the studio voice** once: it makes its own Python
  environment in `data\voice\studio\` (so its libraries never touch CBX's), installs PyTorch for CUDA 12.8 (the
  RTX 6000 needs it) and chatterbox-tts, and starts it; the model (~1.5 GB) downloads from Hugging Face on its
  first start. About 4 GB in all. It runs on 127.0.0.1 only, with a token, and CBX starts and stops it.
- **Quick** (Kokoro, on the CPU, instant): British women (Charlotte, Emma, Harriet, Imogen, Isabella, Alice,
  Lily), British men (George, Fable, Lewis, Daniel), American (Heart, Bella, Nicole, Michael).

All of them are made-up voices: Charlotte, Harriet and Imogen are mixes of Kokoro's own voices, and each studio
voice copies the sound of a short sample that Kokoro speaks — never a real person's recording. (Chatterbox puts an
inaudible watermark in what it makes.) If the studio voice isn't running, her quick twin of the same name speaks.
English only for the studio; Portuguese and French use Kokoro's voices, Arabic the browser's.

**Type and she speaks**: next to Send there is **Speak: off / on** — on, she says every answer out loud in her
voice (remembered in that browser). Long answers are spoken sentence by sentence, so she starts at once.

`.env`: `CBX_VOICE_STUDIO=false` keeps the studio voice off; `CBX_VOICE_STUDIO_PORT` (8099) if that port is taken.

### Support leads, Vendor portal on its own, and fixes for the server's test run (1.0.67)

**Support → Settings → Support leads**: not everyone is a support lead. Add the people who are (pick a person,
Add), tick the ticket types each one covers (CBX-Connect, Chase, Vendor portal …), or remove them. Only people
on this list see the Support menu and can be given tickets; when assigning, the leads who cover that ticket
type are listed first, then every other lead. A lead who covers nothing can still be given any ticket by hand.

**Ticket types fold open**: CBX-Connect, Chase and **Vendor portal** — now a ticket type of its own (registering,
signing in, uploading invoices or documents, payments, updating details) — each fold open to edit their
problem areas and questions; Settings remembers which are open.

Also 1.0.66 with two fixes found by the server's test run on Windows: a tool's data file (`data\tools\*.sqlite`) is
now closed after every use, not just committed — Windows can't remove or replace a file that is still open —
and the Entity Compliance tests empty that file when Windows won't let them delete it (a left-over company
switched the Home page's compliance count over to the tool and failed `test_home`). The test run no longer
reinstalls pywin32 every time.

### Support tickets, and mail through Outlook (1.0.66)

**Ticket request** (everyone, in Workspace): report a problem in three short steps — what it is about
(CBX-Connect, Chase, or any system added in Settings), your details (name, surname, e-mail and department
come from your account; where you are is asked once and remembered), and the problem: pick the kind of
problem and answer its questions (Vendor portal → Uploading invoices asks for the vendor, the vendor number, the
invoice, what you were doing, the error). Screenshots and files can go with it. **Requests** is now for the owner and
admins only; everyone else uses Ticket request.

**C-EXA reads every ticket.** If something essential is missing she asks — in the ticket and by e-mail —
at most three times; the answer (in the app, or a reply to her mail) brings it back to her. When it is
complete she writes a summary for the support lead, and the owner and admins are told it is ready to assign.
What people write is their words, never instructions: her reading has no hands, and she never asks for
passwords, PINs, codes, bank or ID numbers.

**Support** (a new menu section): **Dashboard** — open, pending (waiting for the requester), ready to assign,
being discussed and closed, per system and per lead (open, done, done in 30 days, average days to close);
**Tickets** — the list with filters, and each ticket: the requester's details, C-EXA's summary, the
conversation, **Assign** (owner/admin: pick a support lead — they get the summary by e-mail), write to the
requester, add a note for the team, mark as being discussed, **Close** with what was done (the requester is
told); **Settings** (owner/admin) — the systems, each one's problem areas and their questions (short answer,
long answer or pick-one; required or not), and its support leads. Leads see the Support menu for their systems.

**Tickets by mail.** With Outlook open on the CBX server and the support mailbox in it, every new mail in its
Inbox becomes a ticket (or an answer, when the subject carries the ticket number and it comes from that
ticket's requester). The sender gets the ticket number back and anything C-EXA still needs. Read mail gets the
Outlook category "CBX"; nothing is moved or deleted. Automatic replies, bounces, our own mail and floods (more
than `CBX_SUPPORT_MAX_NEW_PER_SENDER` new tickets an hour from one address) are skipped.

**All of CBX's mail through Outlook** (until SMTP works): `CBX_MAIL_TRANSPORT=outlook` sends every message
— codes, reminders, tickets — from the mailbox in `CBX_OUTLOOK_MAILBOX` through Outlook on the server.

`.env` on the server (Outlook open, signed in, the mailbox added under File → Account settings):

    CBX_MAIL_ENABLED=true
    CBX_MAIL_TRANSPORT=outlook
    CBX_OUTLOOK_MAILBOX=cbx-connect@cbreexcellerate.com
    CBX_OUTLOOK_FOLDER=Inbox
    CBX_SUPPORT_MAIL=true

Outlook must run as the same Windows user as CBX (run.bat in that user's session). If Outlook warns that "a
program is trying to send e-mail", the server's antivirus is out of date — Outlook only allows it silently
when Windows reports the antivirus as current. `pywin32` is installed by the update.

### The office: agents at their desks (1.0.65)

C-EXA → Jobs & teams → Teams → **Office** (now the first view; Chart and Cards are still there). It is
laid out like the company chart: C-EXA's desk on top, looking down at her teams; under each team its lead,
and the agents in the column below, looking up. Every agent has a desk with a computer, a plant and an open filing rack, and you can see at a glance what each one
is doing — live, every 15 seconds:

- **Working** — seated at the computer, typing; code scrolls on the screen and the task shows underneath.
- **Needs you** — stands up and looks at you, a "!" over its head; answer it on Now.
- **Idle** — keeps busy: looks around, plays a quick game, files, waters its plant.
- **Team switched off** — lights out, empty chair.

The **filing rack** holds a folder for every job the agent has had (the open ones edged green); more work
means more, fatter folders, and a pile on top when the rack is full. The **plant** grows with the work
done in the last 30 days and droops after two weeks with nothing finished; click it and the agent waters
it. Click a desk to see what the agent is doing and give it work.

### Talk to C-EXA, and her calls inside CBX (1.0.64)

**Set it up once** (owner): C-EXA → Jobs & teams → Now → **Voice → Set up voice**. It installs Whisper
(faster-whisper, her ears) and Kokoro (kokoro-onnx, her voice) into this Python and fetches Kokoro's two
files (~340 MB) into `data\voice\`. Whisper downloads its model the first time she listens. On the 5090
she listens on the graphics card (`large-v3-turbo`); without one, on the CPU (`small`). Her voice runs on
the CPU and is quick. Nothing leaves the server, no audio is kept — only the words, where a feature keeps
words (a chat message, a call's note). Until it is set up, the browser's own speech is used, as before.

**Talk**: the **Talk** button next to the mic (the C-EXA drawer, and Talk to C-EXA). Press it and speak;
she answers out loud and listens again. Talk over her and she stops. Press it again, or say "stop".

**Calls inside CBX**: ask her ("call Thandi and ask whether the Telkom run went out") — owners and admins
only — or press **Call** when one of her agents suggests a call on Now. It rings in that person's browser
while CBX is open (a 60-second ring, `CBX_CALL_RING_SECONDS`). They press Answer, she says who asked and
why, asks up to six questions one at a time, and listens — they speak or type. The note (each question
and what was said, or "didn't answer" / "said not now") goes to whoever started it; an agent's call goes
back into that agent's work. She never asks for passwords, PINs, codes, bank or ID numbers — a call that
would is refused before it rings. Teams calls and real phone calls are not part of this.

`.env`: `CBX_VOICE=false` switches it off; `CBX_VOICE_STT_MODEL`, `CBX_VOICE_STT_CPU_MODEL`,
`CBX_VOICE_TTS_VOICE` (e.g. `af_heart`). English, Portuguese and French are spoken by Kokoro; Arabic
by the browser.

Still to come: Excel in and out, moving rows between tables and tools, and dragging the
blocks of a page into place in the Builder.

### Entity Compliance moves into the Builder; the agents' company chart (1.0.63)

- **Entity Compliance is a Builder tool now, all the way down.** Its companies, document types,
  documents and "who may see which company" are the tool's own tables, so you change any of it in the
  Builder: add a column (to companies, to documents), change who sees what, which files a document
  takes, how many earlier versions it keeps. At the first start of this version everything is copied
  in once — every company, document type, document, its current file and the files it replaced, who
  filed it, who was given which company, the reminders already sent. The old screens stay, read-only;
  old addresses open the tool.
- **The grid looks the same, and the responsible person is back** next to each company (dark with
  "account" when they have one here; click a name to see only theirs). A dot opens its document — the
  file, the earlier versions, the date, "doesn't expire", "required", notes — and an empty dot starts a
  new one with the company and type filled in. A company's name opens its own page: its row of dots, its
  documents, its details and who may see it. **Companies & types** holds the lists.
- **Mail** on every document: type a message, it goes to the responsible person (or anyone CBX knows)
  with the document, its status and a link straight to it.
- **Who sees what**: a company is "Everyone with the tool" or "Only its people" (Who may see which
  company). Someone given the tool "only for what is theirs" sees only their companies. Anyone who may
  see a document may set its date and file its renewal.
- **New Builder parts for every tool**: a grid from your own tables (rows × columns × a status), a
  worked-out "where a dated document stands" column, files that keep earlier versions, "whoever sees the
  row it links to", "everyone sees the rows where …", "anyone who may see it may change it", a Mail
  button on rows, and pages that open on one row (a company's page).
- **Agents → Teams → Chart**: the company chart of C-EXA's agents — you, C-EXA, her teams across, each
  team's lead and agents down. Live: whoever is working pulses, whoever waits for you has a dot, a team
  that is off is faded. Click anyone to see what they are on and give them work.
- Next (1.0.88 — voice came first in 1.0.64, the office in 1.0.65, support tickets in 1.0.66): Excel in and out of any table, copying or moving rows between tables and tools, and
  dragging parts around a page in the Builder.

### C-EXA comes alive: agents that work with tools, a heartbeat, lighter models (1.0.62)

- **Her agents now do the work themselves.** Each one works like Hermes: it plans, calls its tools, reads
  what came back, calls the next, and finishes with a result and how sure it is. It hands parts to a
  colleague ("a developer of Change", "the Fix lead") and waits for their answer. When it is not sure,
  or the choice is yours, it asks you instead of guessing. It keeps lessons for next time.
- **Agents → Now** (the first tab) shows it all: her heartbeat and what she is doing, **For you** (her
  questions, agents she wants to hire, models, tasks she suggests for people — one press each), what is
  at work and what finished, and every step behind each piece of work. **Give work** hands any agent a
  job; in the chat you can say "have the Fix lead look at batch 17".
- **The heartbeat.** Every 15 minutes a light check (no model unless something is new): pulse alarms,
  new errors, failed rules, broken tools — each handed to the agent whose work it is, once. Every night
  at 01:30 a deep check: one tool a night ("could this be better?" — small fixes in its Builder draft),
  the models, the code behind the day's errors, and how the agents did. Fixes go into drafts and
  sandboxes only; you release and promote. A team that is switched off gets nothing — C-EXA does its
  share herself, so switch Fix and Operations on (Agents → Teams) to let them share the work.
- **Lighter models.** Four kinds of bot, each with its own model: C-EXA herself (`.env`), workers
  (leads, developers, analysts), light (helpers, writers) and eyes (the designer). Agents → Now →
  Models shows what is installed and what each can do, suggests a better fit, and downloads a model from
  Ollama's library when you press Download.
- **Web search.** Put a free ollama.com key in `.env` as `CBX_OLLAMA_API_KEY=` and restart. Her
  searches never carry names, e-mail addresses or long numbers (they are refused), every one is in the
  audit trail, at most 30 an hour, and an agent that has read the web loses its changing tools for the
  rest of that work. Without a key she stays off the web.
- **Still never:** release, promote, grant access, sign in as anyone, read or write a tool's rows on
  her own, API calls or a shell. She also calculates every sum with a tool now instead of in her head.

### C-EXA works in your tools (1.0.61)

- **Rows and tasks, for anyone who may ask her:** "give Thandi a task to check the Telkom bill run by
  Friday", "what are my tasks", "mark the board note as Doing", "find the jobs for Rosebank". She works
  in the tools built in the Builder (To-do and the rest), as the person asking — only the tools,
  tables and rows that person may open, with the same checks as the screen. Names, dates ("next
  Friday", "9 Oct") and pick-list values are read for you; anything she can't read, she says.
- **Changing a tool, for admins and owners:** "add a Who filter to the Team board", "add a Priority
  column (Low, Normal, High) to tasks", "switch off the Take it rule". These go into the tool's
  **Builder draft only**, never live. Open Admin → Builder → the tool, check it, then Release and
  Promote on Versions — a person always does that part.
- She still never releases, promotes, grants access or writes code. Her old to-do hands (for the
  built-in To-do that is being retired) are gone; the new ones replace them.

### "database is locked" fixed at the cause (1.0.60)

- The errors of 30 Sep came from one thing: a model call (a PDF Studio batch, C-EXA's teams, an interview
  answer, Ask C-EXA) was made while its job still held the database's one write lock, so everyone else
  waited 30 seconds behind it and then failed. The record of each model call is now written on its own,
  the jobs save their work before asking the model, and if any code path still asks while holding the
  lock, the server saves first and writes the place to the log (`cbx.gateway`). The test suite now
  fails any such call, which is how the places were found.
- When a save still cannot happen, the person gets "The server was busy and did not save that. Try
  again" (503) instead of a page of errors in the server window and a 500.

### Other systems read our data with a key (1.0.59)

- **Open a table to another system** (e.g. a finance system that wants the jobs): in the Builder, the
  table → *Other systems may read it* → tick the columns (never files) → release. Then Settings → API
  connections → *Keys for our data* → *Make a key*: who it is for, the table, how long (90 days; a year at
  most), calls a minute (60), and optionally the addresses it may come from. The key is shown **once** —
  only its fingerprint is kept; lost means *Revoke* and make another.
- The other system calls `GET /api/shared/<tool>/<table>` with `Authorization: Bearer <key>`: the ticked
  columns, reading only, 1000 rows a call at most, `after_id` for only what is new. Refused over plain
  HTTP unless it comes from the server itself (`CBX_DATA_KEYS_HTTPS_ONLY=true`). Too many wrong keys from
  one address (20 in ten minutes) and that address waits. Keys made, refused and revoked are in the audit
  trail; the owner's document lists what is open and every key (never the key itself).

### Nightly backups and the owner's document (1.0.58)

- **The data is backed up every night** (01:30, 14 days kept): the main database and every tool's tables
  (copied safely while the server runs), the key that seals stored secrets, branding and Builder drafts;
  the uploaded files are mirrored beside them (only what is new or changed). Folder: `data\backups\data`,
  or set `CBX_BACKUP_DIR` in `.env` to another disk or a synced share (better: a lost disk then does not
  take the backups with it). `CBX_BACKUP_AT`, `CBX_BACKUP_KEEP_DAYS`, `CBX_BACKUPS=false` to switch off.
  A backup holds everything the server holds — protect its folder like the data folder.
- **The owner's document**: Admin → Versions → *The owner's document & backups*. Compiled by the server —
  where is what, the settings in force (never a secret's value), every tool (tables, rules, the server jobs
  they run, its README), who owns what, the releases, the backups, and how to change, release, roll back
  and restore; with *Back up now*. Download as HTML (prints well) or Markdown. A copy without people,
  releases or backups is sealed into every release zip as `docs\OWNERS-DOCUMENT.md`.

### PDF Studio in the Builder (1.0.57)

- **Any tool can read PDFs with PDF Studio:** a rule step *Read a PDF with PDF Studio* (pick one of your
  templates) puts the template's columns into the row, with how sure it is and what to check. The batch
  stays in PDF Studio, so answering its questions still teaches the profile; *Fetch what PDF Studio now
  knows* brings the corrected values back.
- **Builder → New tool → It starts from: PDFs read with PDF Studio · <template>** builds the whole tool
  (the table with the template's columns, a form for the PDF, the rules, the button) — ready to release.
- PDF Studio's own screens (reading batches, answering its questions, profiles, Harvest, master sheets)
  stay as they are: they are where it learns.

### Site Schedules and IFRS 16, rebuilt in the Builder (1.0.56)

- **IFRS 16 is a tool folder now** (`tools/ifrs16`): upload the source files once (they are read for the
  months they hold), press *Work out a month* on the upload and give the month — the import workbooks
  (one zip) and the monthly report land on the month's row when the engine is done; the ledger check
  has its own page. The lease engine is unchanged; the Builder runs it as server jobs in the background.
  **The kept uploads, months and ledger checks are copied in once at the first start.**

- **Site Schedules is a tool folder now** (`tools/site-schedules`): drop the Swiftnet quotation, press
  *Read it*; the row fills in and what is unclear becomes lines to tick; *Build the agreement* makes the
  Word document in the row. The built-in page is replaced (menu row and old address open the new one);
  **the old quotations, with their PDFs and agreements, are copied in once at the first start.**
- **New Builder parts, for any tool:** *Run a server job* (Core's know-how as a rule step — reading a
  quote, working out an IFRS 16 month; slow jobs run in the background and the page shows the row
  when it is done; a job can take its files from another row and mark the row when it fails), *Make a Word document* (a .docx template in the tool, filled from the row), *Stop
  unless … (and say why)*, numbers shown as money, tables without an Add button, a button that asks for
  a month.
- **Entity Compliance is one menu row:** the folder's grid (click a dot to file or renew a document, as
  before); *Companies and settings* opens the built-in screens. The second "Entity Compliance · new" row
  is gone.
- **To-do:** goals can count *the team together* (its team tasks done in the period) as well as each
  person; sizes and pick-list words on cards show in the person's language.
- **Words:** the Builder's Words panel has *Let C-EXA translate the missing words* (the local model fills
  the empty boxes; you read them before you save).
- **The AI's name:** typing **AXL** in Settings → Branding now stays AXL (before, it was swapped back to
  C-EXA as if it were the old default).

### To-do, rebuilt in the Builder (1.0.55)

- **To-do is a tool folder now** (`tools/todo`), made only of Builder parts: *My to-do* (my list, team
  tasks I'm on), *Team board* (drag cards; *Take it* on an open task), *Jobs* (across teams), *Stats*
  (this week, this month, per size, per person) and *Goals* (a bar per goal; *Book my reward day* once
  reached). It replaces the built-in To-do page: the menu row and the old address open the new one.
  **Your old tasks, jobs, goals and reward days are copied in once, at the first start** — who added
  each and when kept; the old tables are left as they were. Details: `tools/todo/README.md`.
- **What To-do needed, the Builder can now do for any tool:**
  - *Who sees the rows:* **the members of …** (e.g. a job's people, once they are Active) · each person
    their own **and the whole team sees the rows where** a pick-list has one value (Kind: Team).
  - *Who may add rows:* **managers and admins**. *Only once:* columns that go together once (one reward
    day per goal and person).
  - *Starts as:* a new row's pick-list option, **today** for a date, **me** for a person — shown filled
    in on the form. A part can also say what its new rows start with (the Team board's are Kind: Team).
  - **Progress** part: each goal with a bar — how many rows of another table count towards its target
    ("Done, by me, between the goal's From and To, of the goal's size — empty is any").
  - Buttons on rows can **ask first** (a date, a line), and can be **only once the target is reached** —
    checked by the server, wherever the rule is started from.
  - Rules: an approval can have steps for **declined**; ask **a person in the row** or **their team
    lead** (a lead adding their own people is not asked); a rule can look at **the whole list** it found.
- **Builder teams** may now use charts and boards, and write the new table settings.

### Builder teams, and the parts To-do needs (1.0.54)

- **C-EXA's Builder teams know the Builder.** Build and Change are now named Builder teams; every plan
  and change carries a short guide to what the Builder can show (its parts, sources, column types and
  the rule shape), and the team is held to it: a rule the Builder could only show read-only is sent back
  to be rewritten; the tester fails it too. **Fix** now also finds tools the Builder says are broken
  (a folder that does not load, a draft with problems, a rule it cannot open) and hands them to
  **Change**, which fixes a broken live tool in a copy draft.
- **New column types:** *A person* (from everyone, or the person's own team; shows the name), *A
  checklist* (steps with ticks; shows 3/7), *Worked out* (how many checklist items, how many ticked, days
  between two dates, or a word for a number — e.g. up to 3 Small, up to 7 Medium, more Complex).
- **Who sees the rows:** everyone · each person their own (rows they added or are named in; a manager
  also their team's) · **each team its own**.
- **Board** part: cards in columns, one column per pick-list value; drag a card to move it.
- **Buttons on each row** of a table: a rule started from a row gets that row (e.g. *Take it* → the
  person becomes the assignee). Conditions can say **is me**, and **is this week / month / year**.

### The Builder on one screen (1.0.53)

No tabs, no canvas, no wiring, no code (`docs/tool-folders.md` §0 has the picture):

- **Left, the tool as an outline:** Pages (and the parts of the page you are on) · Data · Rules ·
  The tool (name, menu, who uses it, words in four languages, About, Release). What to fix before a
  release is listed at the bottom, in sentences.
- **Middle, the page as people see it,** with live rows. Click a part to change it; **+ add here**
  between parts to add one, from folders: Show (table, chart, numbers, compliance grid, heading, text,
  picture, links) · Collect (form) · Do (button) · Arrange · Advanced (website, code). *Try it* to use it.
- **Right, plain numbered questions:** *Take the data from* This tool · **Warehouse** (its master sheets —
  new) · Another tool · API connection → *Which data* → *Every row or totals* → columns → *only rows
  where …* → sorted by → filters and search. A sentence on top says what it will show.
- **Rules instead of the flow canvas:** WHEN · ROWS (go through a table, a sheet or another tool's
  rows) · IF · THEN · AFTER, with `{Site}`-style words in messages and *Try it* (nothing is written or
  sent). Existing flows open as rules; one whose shape does not fit opens read-only and keeps working.
- New blocks: **Chart** (bars: rows counted or a number column added up, per value of a column) and a
  table's **Totals only**. New tools, tables, pages and columns take their names from the words you type.
- Warehouse rows follow the row rules: a person sees only the master-sheet rows their rules allow.
- C-EXA's team writes its flows in the rule shape, so what it builds opens here too.

### A quieter menu (1.0.52)

The first step of the rework (the map: *CBX-Connect Map*). Pages that belong together are one menu item
with a row of names at the top; nothing stored changed, and every old address still opens its page.

| Menu item | Holds |
| --- | --- |
| Requests | My requests · New request · Results |
| Work (admins) | Requests · Develop |
| People & access | Users & roles · Org chart & access |
| C-EXA | Talk · Jobs & teams · Developer (the board's view sees Jobs & teams only) |
| Versions | as before, now under Admin |
| Settings (foot) | Mine · API connections · Mail · Data warehouse (the last three for owner and admin) |
| Help (foot) | the lessons (was Training) |

The six teams on timers (Fix, Operations, Data care, Security, Reports, Knowledge) now start switched
off on a new install; switch each on from C-EXA → Jobs & teams when you want it. Build, Change, Advice
and the council stay on.

### Choice lists, links, files, pages for some roles (1.0.51)

What C-EXA's first real plan showed the Builder was missing (`docs/tool-folders.md` → A Data block):

- **Choice** columns: a pick-list (Status: Open · In progress · Done). Forms show a dropdown, tables a
  pill, and a table can filter by it. Anything not on the list is refused, by forms and flows alike.
- **Link** columns: a row of another table (a task's Project). Forms offer that table's rows by name;
  tables show the name; a table can filter by it. A project that tasks point at cannot be deleted
  until those tasks are changed.
- **File** columns: one attachment per row (evidence, a signed form). Only documents and pictures,
  checked by their contents, 10 MB unless the column says (25 at most), kept apart under a random
  name, always downloaded — never opened inside CBX-Connect. Upload, download and removal are audited.
- **Pages for some roles:** a page can be for, say, owners and admins only. Everyone else does not
  see its tab, and its page, its tables and its Run buttons answer *not for your role*.
- In the Builder: a column's type brings its settings (options, which table, which files); the Page
  block has *Who may open this page*. C-EXA's Build team now plans statuses as choices, links tables
  instead of typing names, and can give a page to admins only.

### C-EXA's teams (1.0.50)

C-EXA is the CEO. Under her are **ten standing teams**, each owning one kind of work, each with a lead
and members (Agents & teams → **Teams**; `docs/cexa-team.md`):

| Team | Word | Lead | What it does | When |
| --- | --- | --- | --- | --- |
| Build | Growing | Nova | New tools, as Builder drafts | a job → plan → Go |
| Change | Changing | Mira | Improves a live tool in a copy draft | a job → plan → Go |
| Fix | Fixing | Rex | Groups what broke, says why, proposes the fix | hourly, only when something broke |
| Operations | Managing | Opal | Health, flows, approvals waiting, versions, the model | weekdays 06:30 |
| Data care | Cleaning | Clea | Duplicates, gaps, stale drafts and sandboxes, idle accounts | Sundays 20:00 |
| Security | Protecting | Sage | Failed sign-ins, locks, two-step gaps, admins, AI refusals | daily 06:15 |
| Governance | Reviewing | Gil | The council on every tested version; the access review | each version; the 1st, 07:00 |
| Advice | Advising | Ava | Lists, recommendations, documents | a job → questions → the document |
| Reports | Reporting | Rory | The week for you and the board | Mondays 07:00 |
| Knowledge | Teaching | Kai | Where everything is; README and translation gaps | Saturdays 20:00 |

- **Every job goes to a team.** Give C-EXA a job and she picks the team (or you pick it). Build and
  Change still wait for your Go; Advice writes straight away; a standing team's check runs at once.
- **A standing team's run:** gather (reads the system — counts and names, never a row's contents) →
  analyse (findings with evidence) → check (a second agent keeps, drops or re-weighs each) → write the
  report → the lead's summary. A quiet run costs no model calls. **Nothing a team finds changes
  anything:** each finding has *Hand it to…* Change, Build, Advice or Core code (a proposal) — your
  click.
- **The council:** when a version passes its tests, Governance's three reviewers (security, correctness,
  the people who use it) each read the change alone, then read each other's findings and agree or
  dispute; the verdict follows a fixed rule — a high finding that stands, or a block that stands, blocks.
  Versions shows *Council: passed / concerns / blocked*; **Promote on a blocked version asks you to
  override it on purpose** (logged as `version_council_overridden`). The council never approves anything
  onto live.
- **You shape the teams:** rename, add or remove agents, change a lead, a role, a member's model, the
  schedule, the accountable person, or switch a team off. *What every team knows* is the rules every
  agent's prompt carries (the policies).
- **The board's view:** a role with **See C-EXA's teams** (Users & roles → Roles; the Viewer preset has
  it on new installs) opens Agents & teams read-only — no prompts, no buttons.
- **The first-job fix:** a new job is *read* first; the question box appears only once C-EXA has
  questions (1.0.49 showed an empty one and your answers got lost).
- `CBX_TEAMS_AUTO=true` (default): the schedules and the council run on their own. One GPU: team model
  calls go one at a time.

### AXL is C-EXA, and C-EXA runs a team (1.0.49)

**The name.** AXL is C-EXA everywhere: every screen, message, prompt, document and the code itself
(`cbx/axl/` → `cbx/cexa/`, `/api/axl/…` → `/api/cexa/…` — the old addresses still answer, `#/axl` links
still open). What the database stores keeps its old spelling so nothing is lost: table names
(`axl_messages`, `axl_proposals` …), columns (`is_axl`, `from_axl`, `assignee_axl`) and stored markers
(`"axl"` as the author of a note, `axl→agent:…` in the audit trail — shown on screen as C-EXA). A `.env`
that still says `CBX_AXL_MODEL`, `CBX_AXL_HANDS` or `CBX_AI_NAME=AXL` works: AXL now means C-EXA.
The name is still Settings → Branding. *Uploading this version: type* `branding change` *in the Notes
box* — the version check refuses a package that renames the AI unless it is told so.

**The team** (Agents page, owner only; `docs/cexa-team.md`). Give C-EXA a job — *"build a Project
Manager tool"*. She reads it, asks what she must know, and writes a plan: the tool, its tables, pages
and flows, any core-code change, and the team — developers, a designer, a tester, a helper, a core
developer when there is core code — with every task and who has it. **Nothing is built until you press
Go.** Then the agents work the tasks: the developers write the Builder draft (tables, pages, flows —
flows ship switched off), the helper makes up sample rows, translates and writes the README, the
designer screenshots each page with the made-up rows and scores it against CBX-Connect's look (below
75 she sends it back for one round of fixes), the tester checks the draft loads, every page draws and
every flow runs dry, the core developer builds core changes as a proposal in a sandbox copy with the
full test suite. C-EXA writes it up; you open it in the Builder, try it, and release it yourself. You
see everything: who is on what, each task and its small steps, every event, every screenshot, and
behind each model call the prompt and the reply. While it works you can tell the team something, pause
it or stop it; at review, ask for changes (round 2) or close it. She can also be given a job from
Talk to C-EXA. A job may spend at most `CBX_TEAM_MAX_CALLS` model calls (150) before she pauses it.

**What the team never does:** release or promote, grant access, sign in as anyone, write code or embed
blocks into a page, use API connections, or see a real row. Every draft write is in the audit trail as
`cexa_draft_written`, by `axl→agent:<name>`.

**Models.** One GPU answers one agent at a time, so the agents take turns (the page shows who is
waiting). `CBX_MODEL_DEVELOPER`, `CBX_MODEL_DESIGNER` (must see pictures), `CBX_MODEL_HELPER` choose a
model per role; empty = `CBX_CEXA_MODEL`. **Screenshots** need Playwright and Chromium on the server:
Agents → *Screenshots off* → *Turn on screenshots* installs them (about 150 MB, once). Without them the
designer reads the layout as text.

### Flows and Jobs; Workflows (Node-RED) removed (1.0.48)

Edwin: "do it, make the Builder the best ever — then we need to remove Workflows."

**A flow** is *when this happens, do these steps*, kept in the tool folder as `flows/<name>.json` and
built on the Builder's new **Flows** tab: a trigger and its steps on a canvas, wired port to port.
**When** (the trigger): a **Button** (a *Run button* block on a page), a **New row** or **Changed row**
in one of the tool's tables, or a **Schedule** — the **Job**: every hour, every day (or chosen days),
every weekday, once a week, once a month, at a time in South African time. **Then** (the steps): *Find
rows* (Core data or the tool's own table, with filters), *If* (yes / no), *For each* (the steps on its
*each* port once per row, then *then*), *Add a row*, *Change a row*, *Set a value*, *Message* (in CBX),
*Email* (only to people CBX knows or the company's domain — the mailer's rule), *Approval* (the run
stops until the people asked approve or decline — they get it in **Approvals**, a new menu item with a
count), *Wait* (minutes, hours, days), *Call an API* (a connection ticked for the tool), *Stop*. Every
text box takes `{{values}}` — the chips under a step's settings are the values it can use (the trigger's
row, what earlier steps found, the person it runs as, today); click one to insert it.

**What holds.** A flow runs with the rights of the person who started it, or of the tool's owner (a
schedule always; a button when you choose "the tool's owner") — the same providers, row rules and mail
checks as the screens. Rows a flow writes never start another flow. At most 1 000 steps a run and 500
items per For each; a loop anywhere but For each is refused when the folder is checked. Every run is
kept with every step (who started it, as whom it ran, what each step did, where it waits): **Approvals →
All runs** for owners and admins, with *Run again* and *Cancel the run*. **Test (nothing is sent)** on
the Flows tab runs the flow as it stands on the canvas with live data and writes, sends and calls
nothing — each step says what it would have done. Every release tests every flow the same way (the
generated tool test), a sandbox never runs schedules (`CBX_FLOWS_SCHEDULE=false`), and a schedule the
server was off for is skipped, not caught up. New numbers: *Approvals waiting for you*, *Flow runs that
failed*, *Flow runs today*. Entity Compliance ships a first flow, **Expiry reminders** (weekdays 07:00:
documents expiring within 30 days → email the responsible person at 30 and 7 days → a note to the
owner), **switched off** — its own reminders already run; switch those off first, then this on.

**The Builder, better:** undo and redo (↶ ↷, Ctrl+Z / Ctrl+Shift+Z, 80 steps, both canvases), Ctrl+S to
save, zoom on both canvases (− + Fit, Ctrl+wheel), *Find a block* in the palettes, Ctrl+D copies a block
or step, a step pulled while another is selected is wired after it, *Tidy up* for flows, a **Run button**
block, and flows can be the whole of a tool (a folder with no pages is a background tool).

**Workflows (Node-RED) are gone.** The Workflows page, the embedded editor (`/node-red`), workflow
tools, C-EXA's flow drafting, the Node-RED supervisor, `node-red.bat`, its settings and its pulse check
are removed. A request's ticket is now a **build ticket** (Requests → *Open build*): it names the tool
folder that answers the request, *Start it in the Builder*, *Mark as testing*, and — once that tool is
released and promoted — *Mark as live*, which tells the requester with a link to it. The database keeps
the old tables (tickets' Node-RED drafts, workflow runs, workflow tool rows and their grants) so a
rollback still works; the old workflow tools are no longer shown. API connections are now opened to
tool folders, not workflow tools. On the server: run `server-tools\remove-node-red.bat` once (Home
reminds you while Node-RED still answers on 1880). Tests: `tests/test_flows.py` (11), the connections,
requests, home and versions tests rewritten.

### Home as a dashboard, PDF Studio in one place, View as (1.0.46)

Edwin: "I would like the home page to be more of a dashboard, with numbers — even for me as admin it's
too complex, I need to be able to build my own dashboard"; "can't we put Master Sheets into PDF
Studio?"; "I need the ability to see what a user with their current roles can see."

**Home is a dashboard.** Rows of numbers — open requests, documents due or expired, cells to check,
failed sign-ins today, people without two-step, locked accounts, versions waiting… — and one or two
short lists (things to look at, my requests, my PDF batches). Click a number to open the page behind
it. Home is now a tool folder, `tools/home/`, with one page per role (`owner`, `admin`, `manager`,
`user`); a new block, **Numbers** (`type: metrics`), shows a row of tiles, and the numbers come from a
catalogue in Core (`cbx/engine/metrics.py`, 23 of them, each with who may read it). A person only sees
the numbers their role and tools allow — a user never sees sign-in or people counts; someone without
PDF Studio never sees its numbers or its list. **Customise** (everyone): drag a number to move it, ×
to take it off, *+ Add a number* from the catalogue, *Hide this block*, **Save my Home**, *Reset* back
to the role's design — kept with the person (`prefs.dashboard`), never in the folder. **Design this
Home** (owners, admins) opens the folder in the Builder: the Numbers block's settings list the
catalogue grouped, with a line on each; Release → Promote changes the starting Home for that role.
New in the engine: `access.everyone`, `menu.hidden`, a provider's `tool` (a grant it needs), block ids
unique across a folder's pages, providers `core.my_requests`, `core.my_batches`, `core.attention`,
`GET /api/engine/metrics?keys=`, `/metrics/catalogue`, `/home`, `PUT /home/mine`. The old Home's
C-EXA inbox, check-in and getting-started cards are gone from it (Messages still has C-EXA's messages; the
checklist API stays). Tests: `tools/home/tests/test_home_folder.py`.

**PDF Studio in one place.** PDF Studio has three tabs — **Read PDFs · Harvest · Master sheets** — and
Master Sheets no longer has a menu row of its own. Each tab shows for people who hold the tool behind
it (PDF Studio for the first two, Master Sheets for the third; owners and admins hold all); someone who
holds only Master Sheets gets a *PDF Studio* row that opens on that tab. Grants are unchanged, old
links still work.

**View as.** Users & roles → **View as** on a person's row: the app redraws exactly as they see it —
their menu, tools, rows, Home — with a brown bar at the foot: *Viewing as Thandi · User — read-only …
Stop viewing*. It is their real permissions, not an imitation: the owner's session carries
`view_as_id` and every check runs as that person. Nothing can be changed: every request that is not a
read is refused ("You are viewing as … Stop viewing first"), and a read that would normally change
something (mark a message read) is not saved (`cbx/security/viewas.py`). Owners and admins only, only
people they may manage (an admin never views as an admin or an owner), never the AI, at most an hour;
`view_as_started` / `view_as_stopped` in the audit trail, and anything logged meanwhile names the
viewer: `edwin (as thandi)`. Tests: `tests/test_view_as.py`.

### Our own two-step verification (1.0.45)

Edwin: "we will have to enable external people to join, and our 2-step at the moment only supports
people registered to our domain, so Sanlam or Eskom people can't enter — we need a full two-step
verification procedure, in all ways possible." The company's MFA covers the company's domain; this
one covers **anyone with an account here**, and needs nothing from IT.

**What a person sees.** At the first sign-in after this release (and every new account, once its
temporary password is replaced) a set-up screen stands in front of the app: *Authenticator app*
(recommended — Microsoft Authenticator, Google Authenticator, Authy, 1Password, Bitwarden, any of
them) or *Codes by email* where the server allows it. The app path: scan the QR code (or type the key),
type the first 6-digit code, and keep the **ten recovery codes** that are shown once. From then on a
sign-in from a browser the app has not seen asks for the code from the app — no email involved — and
a recovery code stands in for the app on the day the phone is lost. Owners and admins are asked at
**every** sign-in; users' browsers are remembered for `CBX_DEVICE_TRUST_DAYS` (30). Three wrong codes
in a row lock the account until an owner or admin unlocks it (Users & roles → Edit → Unlock).
**Settings → Security** shows the status and offers *New recovery codes*, *Replace the app* (asks the
password), *Use email codes* and, where a second step is not required, *Remove*.

**What an owner or admin does.** Users & roles → Edit → **Reset two-step** (lost phone, new phone): a
reason is required and goes in the audit trail (`mfa_reset`); the person's remembered browsers and
other sessions are signed out on the spot and they set it up again at their next sign-in. The same
dialog has **Access ends on** — an end date for someone outside the company (a supplier, a client);
their sign-in stops at the end of that day (`login_expired` in the audit trail) until the date is
extended or cleared. The People list shows each person's second step (app · email · not set up) and
the end date.

**What is stored, and where.** The app's secret is sealed with the server's key
(`cbx/security/secretbox.py`), never plain; recovery codes are stored hashed; the audit trail carries
`mfa_enrolled`, `mfa_failed`, `mfa_locked`, `mfa_recovery_used`, `mfa_recovery_reset`, `mfa_removed`,
`mfa_reset`. Codes are RFC 6238 (SHA-1, 6 digits, 30 s, one step of drift either way) —
`cbx/security/totp.py`, no third-party library but `qrcode` for the picture. `.env`:
`CBX_MFA_REQUIRED` (`everyone` — the default — `admins` or `none`), `CBX_MFA_EMAIL_FALLBACK` (true),
`CBX_MFA_ADMIN_TRUST_DAYS` (0), `CBX_MFA_MAX_FAILED` (3). **On the day you promote this**, every
person is asked to set up at their next sign-in — you first. Email codes follow the existing rule:
when the email cannot go, the code is shown in the server's window and in Users & roles (the terminal
fail-safe, 1.0.47 — see *Email and sign-in codes*).
Sandboxes run with `CBX_MFA_REQUIRED=none`, so testing a version never asks for a phone. Passkeys
(Windows Hello, a phone's face unlock) are the next step once the public address is settled — they
need a fixed HTTPS name. Tests: `tests/test_mfa.py`.

### The canvas: our own Node-RED (1.0.44)

Edwin: "my goal is to replicate Node-RED, I want Node-RED but our own, I need to pull in blocks and
build my tools." The Builder is now a **palette, a canvas and a settings panel**. Blocks: Page ·
Heading · Text · Stats · Table · Grid · Form · Image · Website (embed) · Code · Links · Divider · Spacer ·
Data table · Core data (a provider) · API connection. Pull one from the palette (click or drag), move it
by hand, **wire** it by dragging a port onto another block's port: source → block → page → tool. Click
a block for its settings on the right; Delete removes what is selected; *Tidy up* lines everything up.
Blocks wired to a page are laid out top to bottom in canvas order, in 1, 2 or 3 columns. The picture is
saved in the folder (`canvas` in `tool.json` and each page) and the folder is what the canvas compiles
to (`web/builder/graph.js`): `pages/<name>.json` gained `layout` (sections → columns → blocks); a
1.0.43 page (`type` + `source`) still reads as one full-width block.

New on a page: **Image** (pictures uploaded into the folder's `assets/`, served only to people with the
tool), **Website** (another site framed inside the page — the app's policy now allows `frame-src
https:`), **Code** (HTML/CSS/JS dropped in — served as its own document with its own policy,
`engine_api.CODE_CSP`, inside a `sandbox="allow-scripts allow-forms"` frame with no origin: it cannot
read CBX's cookies, pages or API as the signed-in person), **Links**, **Stats** (tiles from a grid
provider's summary), **Heading**, **Text**, **Divider**, **Spacer**. New as a source: an **API
connection** from Admin → API connections (`source.api`, a path, where the list is in the answer, which
fields to show) — the key stays on the server, the outbound guard applies, and one block asks the
service at most once every 30 seconds however many screens show it. Preview draws the pages with the
real renderer, unsaved changes included (`/api/builder/tools/{slug}/preview-widget`). The tool block
carries an accent colour and density (a first design knob per tool).

Entity Compliance's two pages are now blocks on the canvas: Stats + Grid over `compliance.grid`, and a
Table over `compliance.documents`. Tests: `tests/test_engine_layout.py`.

### Tools as folders, the Builder, the ten-minute notice (1.0.43)

The first release of the rebuild, in place. Three things:

**A tool is a folder.** `tools/<name>/` holds `tool.json` (name, menu place, who may use it), `pages/`
(what a screen shows: a table, a grid, a form — and from where), `data/` (the tool's own tables, one
SQLite file per tool under `data\tools\`), `languages/` (`en`, `ar`, `pt`, `fr` — every word is a key,
never text in a block), `flows/` (later), `tests/` and `README.md` (the tool's page in the owner's
document). The engine (`cbx/engine/`) reads the folders at start-up, checks them in plain sentences,
and one renderer (`web/views/tool.js`) draws every page. **Entity Compliance** is the first folder,
drawn beside the old page (badge *new*) from the same data through two providers; the old page is
retired once the new one has run two weeks side by side. Arabic pages are drawn right to left; the
language is chosen in Settings → Appearance and applies to tool pages, their menu names, dates and
numbers.

**The Builder** (Admin → Builder, owners and admins): a folder as forms. Tool (name, icon, section,
who), Pages (type, source — a provider or one of the tool's data blocks — columns, filters), Data
(blocks and their columns), Words (every key × four languages, gaps in red), README, Preview (the
saved draft against live data, read only, in any language), Release. A draft lives in
`data\builder\tools\<name>\`; nothing live changes until **Release** packages every draft as the
next version — the sandbox runs the whole suite (tool folders' own tests included), then the owner
promotes on Versions & sandboxes like any other version. The first tool to build yourself: *Sign-in
attempts* — a table page over the provider `core.sign_in_attempts`, filter *registered = no*, in the
Admin section (see `docs/tool-folders.md`).

**The ten-minute notice.** Promote now asks how much notice people get (default 10 minutes; *switch
now* for a security fix). The version is **announced**: every open page shows a bar with the switch
time and a countdown; what a person is typing is carried over the refresh (`web/carry.js`: the page,
the scroll position, every visible field — never a password — kept in that browser for half an hour);
when the timer ends the ordinary promote runs (backup, copy, pip, restart) and each page refreshes
itself once the new version answers, landing on the same page with the same values and a toast that
says so. A page that was asleep gets two minutes' notice of its own. Cancel the update on Versions
before the switch and nothing has changed. `/api/version` (unauthenticated) is what the pages ask.

Also: the test command collects `tools/` as well as `tests/`; `tests/test_engine_tools.py` warns on an
untranslated word or a folder past 40k tokens and fails them when `CBX_STRICT_TOOLS=1` (the switch to
flip in six months); the Entity Compliance tool folder is ~5k tokens.

### A quieter screen (1.0.42)

Edwin: "my side dash is getting too big … less is more." The sidebar is one line of brand (the
organisation and the version moved to the foot, beside your name), sections that **fold** (click a
heading; what is folded is remembered in that browser, the section holding the page you are on stays
open, a folded section still shows the sum of its counts), rows a little tighter, Training and Settings
side by side at the foot, log-out as an icon on your name. Page titles are smaller and their one-line
subtitles shorter; the stat cards are shorter; cards have less padding. Users & roles folds the
role legend away; the Node-RED card is one line when connected and shows the failing step first when
not; the Versions cards say what they must in one line; the floating Ask C-EXA is not shown on C-EXA's own
page, where you are already talking to her. The grid itself — the circles and their colours — is
untouched.

### Training

**Home → Training** lists short lessons for the tools you hold — a person without Entity Compliance
is shown the lesson greyed, with who to ask. A lesson runs on the real screens: a spotlight on the
thing to look at or click, a sentence saying why, and for the doing steps it waits until you have
done it. The certificate you file is really filed — against one of two **practice entities** that
exist for this and are kept off the real grid, the reminders and the counts (`Entity.is_demo`;
`/api/docwatch?training=1` shows only them). Practice PDFs are generated on the spot and hold no
real account, amount or person. Progress is per person; every lesson can be taken again. The owner
can reset the practice entities from the Training page. Lesson scripts live in
`web/training/lessons.js`, the catalogue in `cbx/training/catalog.py`, and a test keeps the two in
step.

### Email and sign-in codes

Reminders, sign-in codes and digests go out through the app's own mailbox. Put `CBX_MAIL_ENABLED`,
`CBX_SMTP_HOST`, `CBX_SMTP_USER` and `CBX_SMTP_PASSWORD` in `.env`, restart, and send yourself a test
from **Mailer → Settings → Send a test** (1.0.79; it was *Settings → Email*). That is the whole setup.

Signing in from a browser the app has not seen asks for a 6-digit code, then trusts that browser for
30 days. It **switches itself on the first time a message really leaves the server** — not when the
settings are filled in. `.env` holding an SMTP host is a statement of intent; it does not mean the app
password is right or that port 587 is open, and arming on intent would refuse everyone at once,
because on day one nobody has a confirmed browser yet. So until a test message actually arrives,
sign-in works exactly as it did before and each one is noted in the audit trail as `login_unverified`.
If sending breaks later, the same fallback applies: people get in, and the gap is on the record.

**The terminal fail-safe (1.0.47).** Letting people in when the mailer is down leaves the door open
exactly when nobody is watching the inbox, so the default is now `CBX_CODE_FALLBACK=terminal`: a code
that cannot be emailed — mail not set up, not proven yet, the app password wrong, the mail server
down — is printed in the CBX window on the server, boxed, with the person's name, the browser and the
reason, and the person is told *"I couldn't email your code, so it is showing in the window on the
server — ask the owner or an admin"*. They still have to type it. Owners and admins also see it at the
top of **Users & roles** (*Sign-in codes waiting*), for the people they manage, so it can be read to
someone over the phone from anywhere; reading them is logged (`login_codes_viewed`). The code is never
written to disk or to a log file, lasts 10 minutes, works once. Audit: `login_code_terminal`.
Authenticator-app users never need this — their code comes from the phone. `CBX_CODE_FALLBACK=let_in`
brings the old behaviour back. Sandboxes and the test suite run with `let_in`.

Changing your password signs out your other sessions *and* makes your other browsers ask for a code
again — the one you are sitting at keeps its trust.

**Sending mail from the tool** (1.0.38). **Admin → Mail (testing)** shows whether the mailbox is ready,
lets an admin write a message to people CBX knows, and lists the **outbox** — every message the tool
tried to send, sent or not, with the reason. On the Entity Compliance grid every document has a
**Mail** button: pick who (the company's responsible person is suggested), type a message, and the
mail carries your words, which document and company it is about, its status, a link straight to the
document and a link to CBX-Connect, signed with your name; a colleague with an account also gets it in
their Messages. Recipients are people CBX knows — accounts here, responsible people on the grid — or
addresses on the company's own domain; nothing else, so the app can never be a way to send mail as
the company to anyone. Links point at `CBX_PUBLIC_URL` when set, else at the address the last admin
signed in from.

### Entity Compliance: files, people and who sees what (1.0.38)

- **Files live with the company.** A filed certificate goes to
  `data\warehouse\Tools\Entity Compliance\<Company>\<Document type>\` — and nowhere else (no copy
  in the shared library). Files filed before 1.0.38 are moved there at the first start, once, and the
  audit trail says how many. Rename a company and its folder follows.
- **One company for one person.** On a company's page, **Who can see it** gives a person that company
  by name. Tick **only their companies** and that is all they see on the grid — the Mozambique
  controller sees Mozambique. Otherwise they keep the ordinary rule (companies open to everyone plus
  their team's) and gain this one. Admins see everything and are never listed.
- **The responsible person is an account.** **Set the responsible person** picks someone with an
  account, or adds a person by name and email — the account is created there and then (temporary
  password shown once), given the tool and this company, and linked. Reminders and Mail about the
  company's documents reach them. Names written down before 1.0.38 are linked to accounts at start
  when the whole name (or the responsible e-mail) matches exactly one account.

### Branding

The product is called **CBX-Connect** by default; the organisation, tagline, the AI's name (C-EXA), the
logo, the browser-tab icon and the accent colour are all settings. The owner changes them in the app
under **Settings → Branding** — upload a logo (white lettering on a transparent PNG looks best: it sits
inside the dark green tile in the sidebar and on the sign-in page), a tab icon, rename the AI — and
everyone sees it on their next page. These are saved in `data\branding\` and survive updates. The
`.env` values (`CBX_PRODUCT_NAME`, `CBX_ORG_NAME`, `CBX_TAGLINE`, `CBX_AI_NAME`, `CBX_LOGO_FILE`,
`CBX_FAVICON_FILE`, `CBX_ACCENT`) are only the install defaults: put your logo at
`data\branding\logo.png` (samples in `web\branding-samples\`) and it is picked up without any upload.
Renaming the AI renames her everywhere — her account, her prompts, every page and message.

---

## 3. The daily flow

**Users**
1. *New request*: the problem, the impact, how they'd like it fixed. C-EXA reads it within a minute.
2. If approved, either C-EXA interviews them in *Messages* (one question at a time, until they say
   they're happy) or an admin books a meeting. Their request moves Sent → New → Interview →
   Working → Testing → Ready on *My requests*.
3. *Entity Compliance*: the compliance register — certificates, permits and contracts per site, with expiry dates
   C-EXA reads from the PDF; In date / Due soon / Expired / Doesn't expire; reminders at the warning window, 30, 7 and 0 days.
4. *IFRS 16*: a separate tool for the lease accountants — upload the LA register and the RR06/ARO06
   movements once, pick a month, and the PIW / MDIW / Rent Review / Terminations import workbooks plus the
   recon report land in Results. The ledger check takes the Lease Accelerator ledger export and gives back
   the consolidated report. (Engine unchanged from the validated defisions_portal version.)
5. *PDF Studio*: pick or upload a template (columns), drop the PDFs, wait, get the sheet. Orange
   cells need a human; anything typed is checked against the PDF.
6. *PDF Studio → Harvest*: point it at a folder instead — on the server, a network share or OneDrive —
   and it runs by itself: every new PDF is read with the source's template, kept as a dated row (with
   the PDF one click away) and appended to that source's own master sheet ("Harvest · Telkom"). Pick a
   date range (this month, last month, custom) and the rows come back at once — nothing is re-read.
   Sources never mix: Telkom's rows live apart from Swiftnet's. Admins add sources (with a "Check
   folder" test), run now, pause, and re-run a range with the current profile (earlier values stay in
   each row's history). Profiles grow one column at a time — add "Guarantee" to a template and only that
   column is authored; a new template on the same PDFs is seeded from the profile that already reads them.
7. *To-do*: your own list, in the team's columns (To do / Doing / Done by default), each task with its
   steps. Put a task on the team board and anyone on the team can **take** it — it lands on their to-do
   and the team sees who has it and how far along it is. Finished work is counted by complexity (small,
   medium, complex, long — worked out from the steps and the span, or set by the lead), per week, month,
   year and lifetime. The lead can set **goals** ("10 complex tasks this month → a work-from-home day of
   your choice", "30 → Pizza Friday"); when a goal is reached each person books their day on the calendar
   inside the tool, or the lead sets the team event's date. **Jobs** are temporary boards for one piece of
   work that needs people from more than one team (a catch-up after an outage): a team lead or admin starts
   one and names its lead; people on the lead's team join at once, anyone else is asked of *their* team lead,
   who approves or declines on their own To-do. Job tasks show on each person's My to-do with a job pill and
   count for them and their home team. When the work is done the lead **finishes** the job — everyone is
   released and the board stays readable for a week (admins keep it for the record).

**Admins**
- *Requests*: every request scored (importance, C-EXA's confidence). Approve → choose C-EXA or human
  interview → build ticket → the tool built in the Builder (pages, flows) → Mark as testing → release and promote → Mark as live (the requester is told where it is).
- *Develop*: the team board. C-EXA reads every card and comments on what she can fix.
- *PDF Studio → Profiles & models*: what C-EXA learned per layout, version history with approvals,
  the model bake-off, spend meter.

**Owner**
- Home: C-EXA's overnight brief, what needs you, team pulse (who's overloaded), her check-in.
- *C-EXA as developer*: Noticed → Building → For your sign-off → Shipped. She never touches the live
  app; you promote.
- *Versions & sandboxes*: upload a version zip from anywhere, it's tested in a sandbox, one button
  to promote, one to roll back. `run.bat` restarts automatically on promote.

---

## 4. Where things live

`data\` (next to the app, or `CBX_DATA_DIR`): `cbx.sqlite3` (database), `warehouse\` (every file:
`Shared library\…` and `Teams\<team>\<person>\Inbox|Work|Results`), `spans\` (PDF text cache),
`versions\` and `backups\` (the injector), `tools\<name>.sqlite` (each tool folder's own tables),
`builder\tools\<name>\` (Builder drafts, not live), `PENDING_UPDATE.json` (an announced update,
until it switches), `secret.key` (session signing — back it up with the db).

**Back up** `data\` nightly (a plain file copy while the app runs is fine — SQLite is in WAL mode).

---

## 5. Updating

From anywhere: **Versions & sandboxes → drop the zip** → it's unpacked and its tests run in a
sandbox → **Promote** → choose the notice (ten minutes by default: every open page counts down and
carries the person's work over the refresh; *switch now* for an empty office or a security fix).
At the server: `update.bat cbx-online-1.0.97.zip`.
To build a release zip from a checkout: `python -m cbx.versions.cli build`.

Promote replaces `cbx\`, `web\`, `tests\`, `docs\` and `tools\` wholesale. If anyone edited a file under `cbx\` or
`web\` by hand on the server, the promote result and the audit trail name it ("2 hand-edited files were
replaced: web/app.js, …") and the edited copies stay in `data\backups\`. `.env` and `data\` are never
touched. A version whose sandbox tests failed shows up on Home → *Needs attention* until it is fixed.

The sandbox runs the **whole** suite and the row names every failing test, so "1 failed" is never a
mystery. The tests never touch this server's own services (Node-RED, mail, the model): from 1.0.30 to
1.0.36 one test found the live Node-RED and rightly said "that should be off", which kept every upload
out of production — that is fixed. **Delete** removes a version that is not live (its files and zip
too). **Promote anyway** puts a version live although a test failed — owner only, after being shown
which tests, and the audit row says it was forced; it is for a test that is wrong for this server, not
for the code. The header always shows the version that is actually running, and a version applied
with `update.bat` or by copying files is marked live at the next start.

---

## 6. For developers

FastAPI + SQLAlchemy backend (`cbx/`), no-build vanilla-JS SPA (`web/`), tool folders (`tools/`, read
by `cbx/engine/`), pytest suite (`tests/` plus each tool folder's `tests/`, run
`python -m pytest -q tests tools`; no GPU or network needed — `CBX_TESTING=1` puts the model gateway
in stub mode). Read `CONVENTIONS.md` before adding anything. Postgres: set
`CBX_DATABASE_URL` and apply `cbx/security/policies.sql` for database-level row security.
