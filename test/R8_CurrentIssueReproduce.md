# R8 — Managers can escape "their own team only"

| | |
|---|---|
| **Risk** | R8 (see `knowledgebase/RISK-REVIEW-2026-10-27.md`) |
| **Version tested** | 1.0.114 |
| **Type** | Manual test, run in the browser on a local server |
| **Status** | **Reproduced** on 2026-10-29 |

## 1. The risk

A **Manager** is meant to see and manage only their own team. Two paths get round that limit:

1. **Viewer account (this test).** The ready-made **Viewer** role ranks below Manager (5 vs 40) and does
   not have the team limit. So a manager can create a Viewer account and, signed in as it, see **every
   person and the whole audit trail**.
2. **Custom role (not tested here).** A manager can build a role below rank 40 that has *see / add people*
   and *change roles* but not the team limit. They then give it to a second account. That account can issue
   temporary passwords and reset two-step for **Users in any team**, which means it can take over those
   accounts.

Neither path reaches Managers, Admins or the Owner. An account can only manage roles ranked below its own.

## 2. Why it happens

| What | Where |
|---|---|
| "Team only" is a permission on the **role** (`team.scope`), not something inherited from whoever created the account | `cbx/models/core.py` — `is_manager` |
| Viewer preset: rank 5, has `people.view`, `audit.view`, `org.view`, `teams.view`, **no `team.scope`** | `cbx/security/caps.py:104-110` |
| Which roles you can give out is checked **by rank only**; the role's permissions are never compared with yours | `cbx/models/core.py` — `can_manage_role`; `cbx/security/roles.py:61-73` |
| Creating an account checks only `can_manage_role`, then places the account on the manager's team. The team has no effect on what the Viewer sees | `cbx/api/users.py:139-196` |
| Users & roles and Audit trail need only `people.view`. They narrow results to the team **only if the reader has `team.scope`** | `cbx/security/deps.py:78`; `cbx/api/users.py:44-52`; `cbx/api/audit.py:31-34` |
| A new role must not include permissions you don't hold, but the team limit is never **required** | `cbx/security/roles.py:90-105` |

## 3. Before you start

- The server is running (`run.bat`), and you can sign in as the **Owner**.
- **Use the same address everywhere.** Use the address shown in the browser's address bar, for example
  `http://192.168.1.27:8090`. A sign-in made at one address (such as `localhost`) is not sent to the other,
  and you get `{"detail": "Please sign in."}`.
- **Two separate browser sessions** are needed, one for the manager and one for the viewer. Check which
  account each window is signed in as with `/api/auth/me` (step 4 and step 10). Incognito/InPrivate windows
  in the same browser can share one sign-in.
- **Second step:** each new account sets a password and a second step at first sign-in. Email is off on a
  local server, so if you choose *email code*, the code is printed in the CBX (`run.bat`) window.
- Optional: to be able to undo the test completely, stop `run.bat` and copy the `data\` folder first.

Below, `<APP>` means the address from the second bullet, e.g. `http://192.168.1.27:8090`.

## 4. Test data

| Account | Email | Role | Team | Created by |
|---|---|---|---|---|
| Bob Beta | `bob.beta@test.local` | User | Team Beta | Owner |
| Mia Manager | `mia.mgr@test.local` | Manager | Team Alpha | Owner |
| Vic Viewer | `vic.viewer@test.local` | Viewer | Team Alpha (forced) | **Mia** |

## 5. Steps

### Setup — as the Owner

| # | Action | Expected |
|---|---|---|
| 1 | Users & roles → People → **Add person**: Bob Beta, `bob.beta@test.local`, role **User**, team **Team Beta**. | Account created; Team Beta created. |
| 2 | **Add person**: Mia Manager, `mia.mgr@test.local`, role **Manager**, team **Team Alpha**. Copy the temporary password. | Account created; temporary password shown once. |

### Baseline — as the Manager (browser session 1)

| # | Action | Expected |
|---|---|---|
| 3 | Sign in as Mia at `<APP>`. Set a new password and the second step. | Signed in. |
| 4 | Open `<APP>/api/auth/me`. | Shows `mia.mgr@test.local`. |
| 5 | Open Users & roles → People. | Only Mia and Team Alpha people. **Bob Beta and the Owner are not listed.** |
| 6 | Open `<APP>/api/audit?limit=5` and search (Ctrl+F) for `"scope"`. | `"scope": "team"` |

### The escape — Manager creates a Viewer

| # | Action | Expected (current behaviour) |
|---|---|---|
| 7 | Still as Mia: **Add person** → open the **Role** list. | **Viewer** is offered. |
| 8 | Create Vic Viewer, `vic.viewer@test.local`, role **Viewer**. Copy the temporary password. | Account created. The Team field is locked to Team Alpha. |

### Check — as the Viewer (browser session 2)

| # | Action | Expected (current behaviour) |
|---|---|---|
| 9 | Sign in as Vic at `<APP>`. Set a new password and the second step. | Signed in. |
| 10 | Open `<APP>/api/auth/me`. | Shows `vic.viewer@test.local`. |
| 11 | Open Users & roles → People (or `<APP>/api/users`). | **Everyone is listed**, including Bob Beta (Team Beta) and the Owner. |
| 12 | Open `<APP>/api/audit?limit=50` and search for `"scope"`. | `"scope": "all"`, plus the Owner's own actions, e.g. `account_created` for Bob and Mia. |

## 6. Result

**Pass / fail rule**

- **Risk present (fail):** at step 11 Vic sees people outside Team Alpha, **or** at step 12 the scope is
  `"all"`.
- **Risk fixed (pass):** step 7 does not offer Viewer to a manager, **or** Vic sees only Team Alpha
  (step 11) and the audit scope is `"team"` (step 12).

**Run on 2026-10-29 (1.0.114): reproduced.** The manager (Mia, Team Alpha) created a Viewer account. Signed
in as that Viewer, the tester could see people and audit entries outside Team Alpha, including the Owner's
actions.

## 7. Clean up

As the Owner, open Users & roles → People and **disable** Vic Viewer, Mia Manager and Bob Beta. Or stop the
server and put back the copy of `data\` made before the test.

## 8. Suggested fix (from the risk review)

- If the creator is team-limited, every role they create or give out must be team-limited too.
- Only allow giving a role whose permissions are a subset of your own: compare permissions, not just rank.

Re-run this test after the fix, using the pass/fail rule in section 6. The custom-role path (section 1, path 2)
needs a test of its own.
