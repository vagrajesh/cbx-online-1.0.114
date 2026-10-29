# R8 Changes — what was implemented

| | |
|---|---|
| **Risk** | R8 — Managers can escape "their own team only" (`knowledgebase/RISK-REVIEW-2026-10-27.md`) |
| **Decision** | `test/R8-Decision.md` — **Option 4**, with every recommended choice for D1–D6 confirmed |
| **Manual test** | `test/R8.md` |
| **Version** | 1.0.114 (version number not changed) |
| **Date** | 2026-10-29 |
| **Status** | Implemented. Before/after test comparison: **1,056 → 1,065 passed, 0 failed** (section 7) |

---

## 1. What changed, in one paragraph

Before this change, a single setting (`team.scope`, "Their own team only") meant two things at once: *sees only
their team* and *is a manager*. Separately, anyone could give out any role ranked below their own. Now:

- The two meanings are separate: **"Their own team only"** narrows what you see, and a new permission,
  **"Leads their team"**, gives manager powers.
- Every account a team-limited person creates is **itself limited to the team**, whatever its role.
- Nobody can give a role with a power they don't hold themselves.
- Managers can only change roles they built themselves.
- Team lists include everyone on the team except admins, so a manager sees their Viewers.
- Existing data is migrated: Vic becomes team-limited, and any roles managers already built are listed for
  the owner to review.

---

## 2. Decisions implemented

| Decision | Confirmed choice | How it is implemented | Where |
|---|---|---|---|
| **D1** | Option 4: the limit follows the creator | Parts A, B and C below | All files in section 3 |
| **D2** | Owner unticks "See C-EXA's teams" on Viewer | **No code.** The subset rule (C1) blocks Viewer until the owner unticks it. **Owner action required** (section 6) | `cbx/security/roles.py` `may_grant` |
| **D3** | Existing accounts made by managers become team-limited automatically, logged | Migration `1114_r8_team_limited_accounts`: one audit entry per account | `cbx/migrate.py:144, 194-214` |
| **D4** | Roles managers already built: list for owner review, no change | Migration `1114_r8_roles_review`: audit entry `role_review_needed`, outcome `pending`. Roles are not changed | `cbx/migrate.py:145, 217+` |
| **D5** | Managers may still build roles, within the new rules | Building is still allowed. C2 adds the team limit automatically; C3 limits editing | `cbx/security/roles.py:86, 112` |
| **D6** | Owner and Admins can switch off "Limited to their team" | `patch_user` refuses anyone else (403, logged). The checkbox is shown only to owners and admins | `cbx/api/users.py:235`; `web/views/users.js:191` |

---

## 3. File-by-file changes

### 3.1 Permissions — part A ("team-limited" vs "manager")

| File | Change | Logic |
|---|---|---|
| `cbx/security/caps.py:30` | New permission **`team.lead`** — "Leads their team" | A manager's powers over their own team: rows in tools, Entity Compliance people, approvals, chat rooms |
| `cbx/security/caps.py:77` | Built-in **Manager** gains `team.lead` | Built-ins are re-synced at every start, so existing managers get it automatically |
| `cbx/security/caps.py:68` | Built-in **Admin** gains `team.lead` | Under the new subset rule (C1), admins must hold every power in Manager to keep creating managers. It gives admins no extra power, because `is_manager` also needs the team limit |
| `cbx/models/core.py:123` | New property **`is_team_limited`** = holds `team.scope` | Used by every check that **narrows** what you see |
| `cbx/models/core.py:129` | **`is_manager`** = holds `team.lead` **and** `team.scope` | Used by every check that **grants** manager powers. Before: `team.scope` alone |

**Which checks moved to `is_team_limited`** (they narrow visibility):

- `cbx/api/users.py`: `_visible_users`, `_grantable_tools`, `_check_scope` message, role list fallback, teams
  list, `scope`, forced team on create, team move, tool-grant check;
- `cbx/api/audit.py:35, 74`;
- `cbx/api/org.py:179`;
- `cbx/api/docwatch.py:52, 274, 294`;
- `cbx/docwatch/people.py:137`;
- `can_manage_user` in `cbx/models/core.py:180`.

**Which checks stay on `is_manager`** (they are powers), unchanged:

- `cbx/security/rls.py:29, 68`;
- `cbx/api/docwatch.py:72, 142`;
- `cbx/docwatch/service.py:242`;
- `cbx/docwatch/tool.py:129`;
- `cbx/engine/flows.py:355`;
- `cbx/api/chat.py:29`;
- `cbx/chat/service.py:90`;
- `cbx/api/engine_api.py:130, 219, 615, 906`;
- `cbx/cexa/tools_ops.py:44`.

### 3.2 The account carries the limit — part B

| File | Change | Logic |
|---|---|---|
| `cbx/models/core.py:83` | New column **`User.team_limited`** (default false) | The account's own team limit |
| `cbx/models/core.py:100` | `User.caps()` adds `team.scope` when `team_limited` is set | Whatever the role allows, the account sees only its team. It does **not** add `team.lead`, so it gets no manager powers |
| `cbx/api/users.py:195` | `create_user` sets `team_limited` when the creator is team-limited | A Viewer created by Mia is limited to Team Alpha. The audit entry says "team-limited" |
| `cbx/docwatch/people.py:142` | Entity Compliance's "add a responsible person" does the same | The only other place a non-owner creates accounts |
| `cbx/api/users.py:217, 235, 273` | `patch_user` accepts `team_limited`, from owners and admins only | D6. A change is written to the audit trail; anyone else gets 403, logged as `user_scope_blocked` |
| `cbx/api/schemas.py:48` | `user_out` returns `team_limited` | For the screens |

### 3.3 Grant rules — part C

| File | Change | Logic |
|---|---|---|
| `cbx/security/roles.py:118` | New **`may_grant(me, role)`** | **C1:** a role can be given only if it ranks **below** yours **and** every power in it is one **you hold**. The owner can give anything |
| `cbx/models/core.py:164` | New **`User.can_grant_role(slug)`** | Calls `can_manage_role` (rank), then `may_grant`. `can_manage_role` is unchanged and still decides whose existing account you may manage |
| `cbx/api/users.py:129` | `me_can_create` uses `can_grant_role` | The Add person Role list shows only roles you may give |
| `cbx/api/users.py:151` | `create_user` uses `can_grant_role` | **The R8 path 1 fix**: refused with 403 and logged otherwise |
| `cbx/api/users.py:230` | `patch_user` role change uses `can_grant_role` | Team-limited people still can't change roles at all |
| `cbx/api/org.py:241` | Org chart role change uses `can_grant_role` | Admin-only today; same rule for consistency |
| `cbx/security/roles.py:112` | `check_caps` adds "Their own team only" for team-limited builders | **C2 / R8 path 2 fix**: any role a manager builds or edits keeps the limit, with a note. Roles with no powers are left alone |
| `cbx/security/roles.py:86` | `may_edit`: team-limited people edit or delete **only roles they built** | **C3**: protects the Viewer preset and admins' roles |
| `cbx/security/roles.py:138` | New `admin_slugs(db)` | Roles whose holders are admins or owners |
| `cbx/api/users.py:48` and `cbx/api/audit.py:21` | Team lists use "everyone on the team except admins" (`notin_(admin_slugs)`) | **C4**: replaces the hard-coded `["user", "manager"]`, so Mia sees Vic and his actions |
| `cbx/cexa/tools_ops.py:55` | C-EXA's `list_people` uses the same team list for team-limited readers | **Requirement R5** ("no path outside the team"). Previously it gave a manager everyone in the company |

### 3.4 Database migrations — `cbx/migrate.py:142-145`

Each runs **once**, at the next server start, and is recorded in `schema_migrations`.

| Migration | What it does | Decision |
|---|---|---|
| `1114_r8_users_team_limited` | Adds the `users.team_limited` column (default 0) | B |
| `1114_r8_team_lead` | Adds `team.lead` to custom roles that have the team limit **and** "Add people" (custom manager roles), so they keep manager powers. One audit entry per role | A |
| `1114_r8_team_limited_accounts` | Sets `team_limited = 1` on every account created by a team-limited person. Repeats so chains are caught. Never owners, admins or the AI. One audit entry per account | D3 |
| `1114_r8_roles_review` | Lists roles built by team-limited people that have powers but no team limit: audit entry `role_review_needed`, outcome `pending`. **No change made** | D4 |

**Checked on a copy of the live database** (the live file was not touched): all four ran, **Vic Viewer** became
`team_limited = 1` with one audit entry, and Mia, Bob and the owner were unchanged. There was no D4 entry,
because no roles had been built by a manager.

### 3.5 Screens (display only — the server enforces everything)

| File | Change |
|---|---|
| `web/views/users.js:22` | `isManager` also follows the server's `scope`, so any team-limited person sees role and team as fixed |
| `web/views/users.js:27` | **"Team only"** label next to the role of team-limited accounts |
| `web/views/users.js:136` | Add person, for managers: *"People you add join your team and see only your team."* |
| `web/views/users.js:191, 200` | **"Limited to their team"** checkbox in Edit, shown only when `can_set_team_limit` (owner or admin) |
| `cbx/api/users.py:131` | New `can_set_team_limit` in the Users response |
| `cbx/api/roles_api.py:45` | New `team_limited` in the Roles response |
| `web/views/roles.js:70` | For team-limited builders, "Their own team only" is shown **ticked and locked**, with *"Always on for roles you build."* |
| `web/views/roles.js:46` | Footer line: *"Roles you build stay limited to your team, and you can change only the roles you built."* |

### 3.6 Tests — `tests/test_roles.py:336-488`

| Test | Scenarios covered |
|---|---|
| `test_r8_a_manager_cannot_hand_out_a_power_they_do_not_hold` | Viewer blocked before D2; S12; S18 (direct API call refused) |
| `test_r8_a_viewer_a_manager_makes_sees_only_her_team` | S1–S8: Viewer offered and created; Vic sees only R8 Alpha; audit `"scope": "team"`; no rooms; Mia sees Vic and his actions |
| `test_r8_a_viewer_an_admin_makes_still_sees_the_company` | S6 |
| `test_r8_a_role_a_manager_builds_stays_limited_to_her_team` | S10, S15 |
| `test_r8_nothing_a_manager_hands_out_can_take_over_another_team` | S11 (as implemented, see section 4), S13: helper refused Bob's password and two-step reset |
| `test_r8_a_manager_changes_only_the_roles_she_built` | S14 |
| `test_r8_only_an_owner_or_admin_lifts_the_team_limit` | D6, S19 |
| `test_r8_a_manager_keeps_every_power_she_had` | S16, S17, R5 (C-EXA's list of people) |
| `test_r8_the_upgrade_limits_what_managers_already_made` | D3, D4, part A migration |

The fixture `viewer_without_agents` performs the D2 untick for the test and restores the preset afterwards.

---

## 4. Differences from the decision document

| Item | Decision doc | As implemented | Why |
|---|---|---|---|
| **S11** — Mia gives a company-wide role an admin built | "Refused: not team-limited" | **Allowed; the account is limited to Mia's team** | Under Option 4 the limit travels on the **account**. Refusing such a role while allowing Viewer (S1–S2), which is also company-wide, would be inconsistent. The person she creates still sees only her team (tested). **Please confirm.** |
| **Admin gains `team.lead`** | Not listed | Added to the built-in Admin role | Without it, the subset rule would stop admins creating managers. No extra power for admins |
| **D2 also affects admins** | Doc mentioned managers only | Until the owner unticks "See C-EXA's teams" on Viewer, **admins** can't give Viewer either | Admins don't hold that permission either. The owner's untick fixes both |
| **C-EXA `list_people`** | Not listed | Narrowed to the team for team-limited people | Found during implementation; required by R5 |
| **Entity Compliance account creation** | Not listed | New accounts inherit the team limit | Second place accounts are created; same rule |

---

## 5. What was not changed

- The **Viewer** and other presets in code: D2 is an owner setting, not a code change.
- Manager powers: rows, approvals, rooms and Entity Compliance work exactly as before (tested, S17).
- The four built-in roles behave as before. The existing parity test passes.
- No data changes outside the four migrations.

---

## 6. Deploying on this server

1. **Back up**: stop `run.bat` and copy the `data\` folder.
2. **Restart** `run.bat`. The four migrations run once; check the audit trail for the `account_changed` and
   `role_review_needed` entries.
3. **D2, as the owner**: Users & roles → Roles → **Viewer** → untick **"See C-EXA's teams"** → Save.
4. **Re-test** with `test/R8.md`. Expected after the fix: step 7, Viewer is offered; step 11, Vic sees
   **Team Alpha only**; step 12, `"scope": "team"`; Mia's People list shows Vic with a **"Team only"** label.
5. **Roll back** if needed: stop the server and restore the copied `data\` folder and the previous code. A copy
   of every changed file as it was before this change is kept at
   `C:\Users\LENOVO~1\AppData\Local\Temp\claude\c--Projects-cbx-online-1-0-114\52f2eb0f-8826-471d-928e-82b746116d45\scratchpad\backup-before-r8\`.
   This is a temporary folder, so copy it somewhere permanent if you want to keep it.

---

## 7. Verification status

| Check | Result |
|---|---|
| `tests/test_roles.py`: 20 existing and 9 new R8 tests | **29 passed** |
| `tests/test_core.py` plus the 20 existing role tests (run before the R8 tests were added) | **44 passed** |
| Changed Python files compile; changed JavaScript files pass a syntax check | **OK** |
| Migrations on a copy of the live database | **OK** (section 3.4) |
| Before/after comparison: 93 of 106 test files in `tests/` plus all 5 tool test files, run on an untouched copy and on the changed code | **Before: 1,056 passed, 0 failed. After: 1,065 passed, 0 failed.** The 9 extra passes are the new R8 tests; no existing test changed result |
| 13 test files not compared | `test_areas`, `test_backup_owner_doc`, `test_cexa_hands`, `test_develop`, `test_engine`, `test_guard_1114`, `test_hands`, `test_hardening`, `test_mfa`, `test_rollout`, `test_sandbox_door`, `test_teams_and_building`, `test_versions`. Each starts a second copy of the test suite in a separate process, which hangs on Outlook on this PC (see the note below). Run them on a machine where Outlook is set up, or has no Outlook |
| `ruff` lint | Not run (not installed) |

**Testing note:** on this PC Outlook 2016 is installed but not set up. Any test that opens Mailer settings starts
Outlook through COM and waits forever on its first-run screen. Tests were run with a small plugin, kept outside
the project, that makes Outlook calls fail fast. The second copy of the suite started by the 13 files above
doesn't load that plugin, so on this PC it opens Outlook and waits. During testing, Outlook was opened this way
twice, both times on its welcome screen with no mailbox, and was closed again. The live app has the same
behaviour: opening **Mailer → Settings** contacts Outlook on the server even when email is switched off.
