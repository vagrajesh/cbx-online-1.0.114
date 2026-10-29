# C-EXA's teams — the owner's page (1.0.49, teams 1.0.50)

Since 1.0.50 C-EXA is the CEO of ten standing teams (Agents & teams → Teams): Build, Change, Fix,
Operations, Data care, Security, Governance, Advice, Reports, Knowledge — each with a lead, members, a
person accountable for it, and (the standing ones) a schedule. The README's "C-EXA's teams" section has
the table. What follows is how a **Build** job runs; Change runs the same way on a copy of a live tool.

## Standing teams, Advice and the council

- **Standing teams** (Fix, Operations, Data care, Security, Reports, Knowledge; Governance's access
  review): gather → analyse → check → write → summary. No Go: they only read and report. Each finding can
  be handed to Change, Build, Advice or Core code with one click; nothing changes until you do.
- **Advice:** questions (if any) → outline → draft → check against the job and the rules → final
  document → you read it; *Ask for changes* is round 2 of the same document; *Download* gives the .md.
- **The council** (Governance, on every version that passes its tests): three lenses review the change
  alone, then agree or dispute each other's findings, then the lead applies the rule: a high finding that
  stands, or a block that stands → **block**; any medium or a concern → **concerns**; else **pass**.
  Promote on a blocked version asks you to override it, and that is logged.
- **What every team knows** (Teams tab): the rules every agent's prompt carries. Keep your policies there.
- **The board's view:** a role with "See C-EXA's teams" sees all of this read-only.
- `CBX_TEAMS_AUTO=false` stops the schedules and the council running on their own (Run now still works).


C-EXA is the head of a small team of agents. You give her a job; she runs it; you release what it made.

## The steps of a job

```
 you: the job ──▶ C-EXA: her questions ──▶ you: answers (or "let her decide")
             ──▶ C-EXA: the plan ──▶ you: Go  (or Change the plan / Stop)
             ──▶ the team works the tasks ──▶ C-EXA: the write-up ──▶ you: open it in the Builder, release it
                                                                  └─▶ you: Ask for changes → round 2
```

Where: **Agents** (AI controller, owner only). Or tell C-EXA in *Talk to C-EXA* — she starts the job
and puts her questions on the Agents page.

## Who is on the team

| Role | Name (default) | What it does | Model |
| --- | --- | --- | --- |
| Lead | C-EXA | Asks, plans, hands out the tasks, checks the work, writes it up | `CBX_CEXA_MODEL` |
| Developer | Dev-1, Dev-2 | Writes the Builder draft: data tables, pages, flows | `CBX_MODEL_DEVELOPER` |
| Core developer | Core-1 | Core-code changes, as a proposal in a sandbox copy (only when the plan has core code) | `CBX_MODEL_DEVELOPER` |
| Designer | Iris | Screenshots each page (made-up rows) and scores it against CBX-Connect's look | `CBX_MODEL_DESIGNER` (must see pictures) |
| Tester | Tess | The folder loads, every page draws, every flow runs dry, the words are there — no model | — |
| Helper | Hal | Made-up rows for the screenshots, the Arabic / Portuguese / French, the README | `CBX_MODEL_HELPER` |

An empty model setting means the same model as C-EXA. One GPU answers one agent at a time: the agents
take turns, and the page shows who is working and who is waiting.

## The tasks, in order

1. **Tables** (Developer) — one task per table.
2. **Flows** (Developer) — after the tables; each flow is checked and **run dry** (nothing written,
   sent or called) before it is saved, and it is saved **switched off**.
3. **Pages** (Developer) — after the tables and flows (a Run button needs its flow).
4. **Sample rows** (Helper) — made up, never real.
5. **Looks** (Designer) — one per page. Below 75/100 she sends the page back to its developer with what
   to change, then looks again (once).
6. **Words** (Helper) — every English word into Arabic, Portuguese and French.
7. **README** (Helper).
8. **Core code** (Core developer) — runs beside the rest: a proposal on *C-EXA as developer*, built on
   its own branch in a sandbox copy, the full test suite run there, fixed and run again if red. When it
   is green it waits for you to ship it from *C-EXA as developer*.
9. **Test** (Tester) — the whole draft. If something fails, the fixes go back to the developers once and
   the test runs again.
10. **Write-up** (C-EXA).

Every task shows its small steps (read the plan → write → check → save …) while it runs. A task that
still fails after three tries is marked **Needs you** with the reason; the rest of the team carries on.

## Seeing everything

- **Team** — each agent: role, model, what it is doing now, tasks done, model calls.
- **Tasks** — To do · Working · Done · Needs you. Click a task to see only its activity and its steps.
- **What's happening** — every event, newest first. Tick *model calls* to see each call; *Prompt &
  reply* opens exactly what was sent to the model and what came back.
- **What came out** — the draft's files, the folder check, the screenshots, core-code diffs.

## What you can do while it works

*Tell the team* (every agent's next prompt carries it) · *Pause* (after the step it is on) · *Carry on* ·
*Stop* (the draft stays in the Builder as it is). At review: *Open it in the Builder*, *Ask for changes*
(C-EXA turns them into tasks — round 2, 3 …), *Close the job*.

## The rules the team works under

- It builds a **Builder draft** (`data\builder\tools\<slug>\`) with a folder name no live tool and no
  other draft has. The Builder shows *Built by C-EXA's team (job n)* on it.
- It never releases, promotes, grants access, signs in as anyone, writes code or embed blocks into a
  page, or uses API connections. Flows ship switched off.
- It never sees a real row. The designer's screenshots are of the helper's made-up rows; the tester
  checks the draft's own (empty) tables.
- Core code only ever changes in a sandbox copy, and only you ship it.
- A job may spend at most `CBX_TEAM_MAX_CALLS` model calls (150). At the limit C-EXA pauses it and
  tells you; *Carry on* gives it 50 more.
- Every draft write is in the audit trail (`cexa_draft_written`, `axl→agent:<name>`).

## Screenshots

The designer takes her screenshots with headless Chromium on the server (Playwright): the app opens its
own page `/cexa-preview/<token>` — a one-time token, two minutes — which draws the draft page with the
real stylesheet and renderer and the made-up rows. She compares it with a page of CBX-Connect as it
looks today (Home). Turn it on from Agents → *Screenshots off* → *Turn on screenshots* (installs
Playwright and Chromium, about 150 MB, once). `CBX_TEAM_SCREENSHOTS=false` switches it off.

## Where it lives in the code

| What | Where |
| --- | --- |
| The team: lifecycle, plan, tasks, workers, runner | `cbx/cexa/ceo.py` |
| The standing teams, gather, reports, advice, the council, the schedule | `cbx/cexa/teams.py` (tables `cexa_teams`, `cexa_team_members`) |
| The designer's eyes (screenshots, the preview page) | `cbx/cexa/eyes.py`, `web/cexa-preview.js` |
| The API | `cbx/api/missions_api.py` (`/api/cexa/missions…`) |
| The tables | `cbx/models/missions.py` (`cexa_missions`, `cexa_agents`, `cexa_tasks`, `cexa_events`) |
| Stub answers (tests, no model) | `cbx/cexa/team_stub.py` |
| The page | `web/views/agents.js` |
| The tests | `tests/test_team.py` |
