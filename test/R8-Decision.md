# R8 Decision — Managers can escape "their own team only"

| | |
|---|---|
| **Status** | **Awaiting decision.** No code has been changed. |
| **Risk** | R8, in `knowledgebase/RISK-REVIEW-2026-10-27.md` |
| **Version** | CBX-Connect 1.0.114 |
| **Evidence** | Reproduced on 2026-10-29. See `test/R8.md` for the steps |
| **Effort if approved** | About 2–3 developer days, including tests |
| **Decisions needed** | Section 5: one approach (D1) and five follow-on choices (D2–D6) |

---

## 1. The problem in plain words

A **Manager** is meant to see and manage **only their own team**. Today they can get round that limit in two
ways:

1. **Through a Viewer account.** A manager creates an account with the ready-made **Viewer** role. Viewer has
   no team limit, so that account sees **all ~1,900 people and the whole audit trail**.
2. **Through a role they build.** A manager builds a role without the team limit and gives it to a second
   account. That account can **reset passwords and two-step for Users in other teams**, which means it can
   take over their accounts.

Both were confirmed by reading the code. Path 1 was also reproduced in a test (Mia, a manager, created Vic, a
Viewer; Vic saw people and audit entries outside Mia's team).

**Why it happens.** The server checks only **rank** when someone gives out a role. It never checks:

- whether the role's powers are within the giver's own powers; or
- whether the role keeps the giver's team limit.

The team limit belongs to the **role**, not to the person. Placing Vic on Mia's team does not limit what he
sees.

**A second issue found during the review.** In this code, "limited to a team" and "is a manager" are the same
setting. Any account given the team limit also gets **manager powers**: it sees the whole team's data in tools,
can change who sees team companies in Entity Compliance, can be sent approvals, and can create chat rooms. So
simply adding the team limit to a Viewer would create a **new** risk.

---

## 2. What the business wants

| # | Requirement |
|---|---|
| R1 | A manager **can** create accounts with the **Viewer** role. |
| R2 | A Viewer created by a manager sees **only that manager's team**: people and audit trail. |
| R3 | A Viewer created by the **Owner or an Admin** still sees the **whole company** (e.g. an auditor). |
| R4 | A Viewer **changes nothing** and has **no manager powers**. |
| R5 | No manager can reach people, data or accounts outside their own team, by any path. |
| R6 | A manager sees and can manage everyone they put on their team, including Viewers. |

---

## 3. Options considered

| | Option | How it works | Meets | Effort | Verdict |
|---|---|---|---|---|---|
| **1** | **Block** | Managers can't give Viewer at all. Only roles within their own powers, and with the team limit. | R4, R5. **Fails R1** | 1–2 days | Safe, but takes away a wanted feature |
| **2** | **Make the Viewer role team-limited** | Tick "Their own team only" on the Viewer preset. | R1, R2. **Fails R3, R4** | Mostly config | Every Viewer becomes team-limited, and each one gets manager powers (section 1) |
| **3** | **"Team viewer" role** | The owner creates a second, team-limited viewer role for managers to give out. | R1 (under another name), R2. **Fails R4** | Config | Same manager-powers problem as option 2 |
| **4** | **Recommended: the limit follows the creator** | An account created by a team-limited person is team-limited itself, whatever its role. "Team-limited" and "manager" become separate settings. | **R1–R6** | 2–3 days | Meets every requirement |
| 5 | Do nothing | — | None | 0 | R8 stays open |

---

## 4. Recommended fix (option 4)

### 4.1 Server (API) — this is what closes the risk

The screens alone cannot close it: the test showed anyone can call the API directly from the address bar.

| Part | Change | Where |
|---|---|---|
| **A. Separate "team-limited" from "manager"** | Add a permission **"Leads their team"** (`team.lead`) and give it to the built-in Manager role. `is_manager` then means "leads their team **and** is team-limited". A new `is_team_limited` means "sees only their team". **Visibility** checks use `is_team_limited`; **manager powers** keep `is_manager`. Existing custom manager roles (team limit + add people) get `team.lead` in a one-time migration. | `cbx/security/caps.py:69-76`; `cbx/models/core.py:115`. Visibility checks: `cbx/api/users.py:48, 58, 113, 123, 155, 227`; `cbx/api/audit.py:31, 70`; `cbx/api/org.py:179`; `can_manage_user` in `cbx/models/core.py`. Power checks (unchanged): `cbx/security/rls.py:29, 68`; `cbx/api/docwatch.py:72`; `cbx/engine/flows.py:355`; `cbx/chat/service.py:90` |
| **B. The team limit follows the creator** | New account setting **"Limited to their team"** (`team_limited`). It is switched on automatically when a team-limited person creates the account, and it adds the team limit to whatever the role allows. Only the Owner or an Admin can switch it off, and doing so is written to the audit trail. | New column on `User`; `cbx/api/users.py:184-186` (`create_user`); `User.caps()` in `cbx/models/core.py`; `patch_user` |
| **C1. Powers must be a subset** | Someone can give a role only if it ranks below theirs **and** every power in it is one they hold themselves. | New `may_grant` in `cbx/security/roles.py`; `can_grant_role` in `cbx/models/core.py`; used at `cbx/api/users.py:122, 143, 219` and `cbx/api/org.py:241` |
| **C2. Roles a team-limited person builds keep the limit** | "Their own team only" is added automatically, with a note on screen. | `check_caps`, `cbx/security/roles.py:90-105` |
| **C3. Managers can change only roles they built** | Managers can no longer edit or delete the Viewer preset or roles built by admins. | `may_edit`, `cbx/security/roles.py:76-87` |
| **C4. Managers see everyone on their team** | Replace the hard-coded `["user", "manager"]` with "everyone on the team except owners and admins". | `cbx/api/users.py:51`; `cbx/api/audit.py:25` |
| **M. Existing data** | Switch on "Limited to their team" for accounts already created by team-limited people (this fixes Vic). List, for the Owner to review, any roles managers have already built without the team limit. | One-time migration and report |

### 4.2 Screens (front end) — display only, after the server change

| Change | Where |
|---|---|
| A line under Role for managers: *"People you add see only your team."* | `web/views/users.js:133-135` |
| A **"Team only"** label on accounts that are limited to their team | `web/views/users.js`, People table |
| A **"Limited to their team"** checkbox, shown only to the Owner and Admins | `web/views/users.js`, edit dialog |
| "Their own team only" shown **ticked and greyed out** when a manager builds a role | `web/views/roles.js:20-23, 65-72` and a `team_limited` flag in `cbx/api/roles_api.py:39-45` |
| The new **"Leads their team"** permission in the Roles list | `web/views/roles.js` |

### 4.3 Tests

New automated tests in `tests/test_roles.py`, plus a re-run of the manual test `test/R8.md` (section 7).

---

## 5. Decisions needed

Tick one box per decision. ★ marks the recommended choice.

**D1 — Which approach?**
- [CONFIRM] ★ Option 4: the limit follows the creator (meets R1–R6)
- [ ] Option 1: block managers from giving Viewer (simpler; fails R1)
- [ ] Other: ______________________

**D2 — The Agents page on the Viewer role.** The Viewer preset includes "See C-EXA's teams" (the Agents page).
Managers don't have that permission, so the subset rule (C1) would still stop them giving Viewer.
- [CONFIRM] ★ The Owner unticks it on the Viewer role (Roles tab; a settings change, not a code change)
- [ ] Keep it on Viewer. Create a "Company viewer" role (rank 50 or above) for Admins, and a Viewer without it for managers
- [ ] Give managers "See C-EXA's teams" as well

**D3 — Existing accounts created by managers (e.g. Vic).**
- [CONFIRM] ★ Switch on "Limited to their team" automatically, and record each change in the audit trail
- [ ] List them for the Owner to decide one by one

**D4 — Roles managers have already built without the team limit.**
- [CONFIRM] ★ List them for the Owner to review (no automatic change)
- [ ] Add the team limit to them automatically

**D5 — Should managers be able to build roles at all?**
- [YES] ★ Yes, within the new rules (C1–C3)
- [ ] No. Remove "Change roles" from the Manager role, so only Admins and the Owner build roles

**D6 — Who can switch off "Limited to their team" on an account?**
- [YES] ★ Owner and Admins
- [ ] Owner only

---

## 6. Scenarios

Mia is a Manager on **Team Alpha**. Bob is a User on **Team Beta**. "After" assumes option 4 with the
recommended choices.

### 6.1 Viewer accounts

| # | Scenario | Today | After |
|---|---|---|---|
| S1 | Mia opens **Add person** | Viewer is offered | Viewer is offered (R1) |
| S2 | Mia creates **Vic** as a Viewer | Created; Vic sees the whole company | Created; Vic is **limited to Team Alpha** |
| S3 | Vic opens **Users & roles** | Everyone, including Bob and the Owner | **Team Alpha only** |
| S4 | Vic opens the **audit trail** | All entries (`"scope": "all"`) | Team Alpha's entries (`"scope": "team"`) |
| S5 | Vic tries to create a chat room, or is due an approval | No manager powers | Still none (R4) |
| S6 | An **Admin** creates a Viewer | Sees the whole company | Still the whole company (R3) |
| S7 | Mia looks for Vic in her People list | **Not listed** (the hard-coded role list) | Listed; Mia can manage him (R6) |
| S8 | Mia reads her team's audit trail | Vic's actions are missing | Vic's actions are included |
| S9 | The **existing** Vic, after the update | — | Migration limits him to Team Alpha (D3) |

### 6.2 Roles built by managers

| # | Scenario | Today | After |
|---|---|---|---|
| S10 | Mia builds a role without "Their own team only" | Saved as company-wide | Team limit **added automatically**, with a note |
| S11 | Mia gives a company-wide role an Admin built | Allowed if it ranks below 40 | **Refused**: not team-limited |
| S12 | Mia gives a role with a power she doesn't hold | Allowed if it ranks below 40 | **Refused**: not a subset of her powers |
| S13 | A Helper account (built and given by Mia) resets **Bob's** password or two-step | **Allowed: account takeover** | **Refused**: Bob is outside Team Alpha |
| S14 | Mia edits or deletes the **Viewer** preset | Allowed | **Refused**: she didn't build it (C3) |
| S15 | Mia edits a role she built herself | Allowed | Allowed, but the team limit stays |

### 6.3 Everyday work and edge cases

| # | Scenario | Today | After |
|---|---|---|---|
| S16 | Mia creates an ordinary **User** | Allowed | Allowed. Nothing changes for that user |
| S17 | Mia's own powers (her team, approvals, Entity Compliance, rooms) | Work | Work exactly as before |
| S18 | Someone calls the API directly, bypassing the screen | Viewer accepted | **Refused (403)**, the same as the screen |
| S19 | An Admin later changes Vic's role | — | Vic **stays team-limited** until an Owner or Admin switches the limit off |
| S20 | Mia is promoted to Admin | — | Accounts she already created stay limited until switched off (D6) |
| S21 | Mia is disabled or leaves | — | Her accounts stay limited to Team Alpha |
| S22 | Vic is moved to another team | — | Vic sees only his **new** team |
| S23 | A manager who isn't on a team tries to add someone | Refused | Still refused |
| S24 | C-EXA (the AI) tries to give a role | Refused | Still refused |

---

## 7. How we confirm it's fixed

Re-run `test/R8.md` on the updated version. The expected results change as follows:

| Step | Today | Expected after the fix |
|---|---|---|
| 7 — Viewer is offered to Mia | Yes | **Yes** |
| 11 — Vic's People list | Everyone | **Team Alpha only** |
| 12 — Vic's audit scope | `"all"` | **`"team"`** |
| New — Mia's People list | Vic missing | **Vic listed** |

New automated tests will cover S2–S8, S10–S16 and S18. The existing test that the four built-in roles behave
exactly as before (`tests/test_roles.py`) must still pass. That is the guard for change A.

R8 moves from **"✅ (mechanism)"** to **fixed and verified** only when both the manual test and the automated
tests pass.

---

## 8. Risks of making this change

| Risk | How it is controlled |
|---|---|
| Changing what "manager" means touches checks in 14 files | Only the visibility checks move to the new setting; the power checks are unchanged. The existing test for the built-in roles, plus the new tests, catch any change in Mia's behaviour (S17). |
| A new database column and a migration | Take a backup before the update (the nightly backup, plus a copy of `data\`). The migration only sets a flag, and it is recorded in the audit trail. |
| An existing custom manager role loses its powers | The migration gives "Leads their team" to every role that has the team limit and "Add people". |
| Managers lose the ability to edit the Viewer preset (C3) | Intended. The Owner and Admins can still edit it. |

---

## 9. Sign-off

| Name | Role | D1 | D2 | D3 | D4 | D5 | D6 | Approved (Y/N) | Date |
|---|---|---|---|---|---|---|---|---|---|
| | Owner / business | | | | | | | | |
| | Security / risk | | | | | | | | |
| | Development lead | | | | | | | | |

Once the decisions are signed off, the changes will be made and `test/R8.md` re-run.
