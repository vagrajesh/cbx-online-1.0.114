# Two-step verification — the owner's page

CBX-Connect has its own second step for signing in. It does not use the company's MFA, so it works for
anyone with an account here: people on the company's domain and people outside it (a supplier's
contact, a client's analyst). Nothing has to be registered with IT.

## 1. What happens to each person

| When | What they see |
| --- | --- |
| First sign-in after 1.0.45 (or a new account, once its temporary password is replaced) | A set-up screen in front of the app. Nothing else opens until it is done. |
| They choose **Authenticator app** | Scan the QR code (or type the key), type the first 6-digit code, keep the ten recovery codes. Two minutes. |
| They choose **Codes by email** (if `CBX_MFA_EMAIL_FALLBACK=true`) | A 6-digit code is emailed at each sign-in from a browser the app has not seen. If the email cannot go, the code shows in the CBX window on the server and in Users & roles → *Sign-in codes waiting* — someone reads it to them (1.0.47). |
| Later sign-ins, same browser | Users: nothing for 30 days (`CBX_DEVICE_TRUST_DAYS`). Owners and admins: the code every time (`CBX_MFA_ADMIN_TRUST_DAYS=0`). |
| Later sign-ins, new browser | Password, then the code from the app (or from the inbox). |
| Phone lost | *Use a recovery code instead* on the sign-in page. Each works once; two left brings a warning. |
| Phone and codes lost | An owner or admin resets it (below). |
| Three wrong codes in a row | The account locks until an owner or admin unlocks it. |

**Settings → Security** shows the person's status and offers *New recovery codes* (needs a live code),
*Replace the app* (needs the password), *Use email codes*, and *Remove* where a second step is not
required for them.

## 2. What you do

**Users & roles → Edit → Reset two-step.** For a lost or replaced phone. Say why — it goes in the audit
trail with your name. The person's remembered browsers and other sessions are signed out on the spot;
whoever holds the old phone holds nothing. At their next sign-in they set it up again.

**Users & roles → Edit → Unlock.** Lifts the lock from three wrong codes (and from five wrong
passwords) and clears the counters.

**Users & roles → Add user → Access ends on.** For someone outside the company. Their sign-in stops at
the end of that day; extend or clear it from Edit. The People list shows the end date and each
person's second step.

**Admin → Audit trail.** `mfa_enrolled`, `mfa_failed`, `mfa_locked`, `mfa_recovery_used`,
`mfa_recovery_reset`, `mfa_removed`, `mfa_reset`, `login_expired`.

## 3. The day you promote it

Every person is asked to set it up at their next sign-in — you first, since you promote. Tell people
the day before: "install Microsoft Authenticator (or any authenticator app) on your phone; CBX will
ask you to scan a code once." A person without a phone app can choose email codes on the screen.

Sandboxes run without it (`CBX_MFA_REQUIRED=none` inside the sandbox), so testing a version never
asks for a phone.

## 4. Settings (`.env`)

| Key | Default | Meaning |
| --- | --- | --- |
| `CBX_MFA_REQUIRED` | `everyone` | `everyone`, `admins` (owners and admins only) or `none` (optional for all) |
| `CBX_MFA_EMAIL_FALLBACK` | `true` | whether *Codes by email* may be chosen instead of an app |
| `CBX_MFA_ADMIN_TRUST_DAYS` | `0` | how long an owner's or admin's browser is remembered (0 = asked every time) |
| `CBX_MFA_MAX_FAILED` | `3` | wrong codes in a row before the account locks |
| `CBX_DEVICE_TRUST_DAYS` | `30` | how long a user's browser is remembered |
| `CBX_CODE_FALLBACK` | `terminal` | a code that cannot be emailed goes to the server's window and Users & roles (`let_in`: sign-in goes ahead without it, the old way) |

## 5. How it is built

Codes are RFC 6238 (SHA-1, 6 digits, 30 seconds, one step of clock drift either way) —
`cbx/security/totp.py`, with `qrcode` only for the picture. The app's secret is sealed with the
server's key (`cbx/security/secretbox.py`) before it is stored; recovery codes are stored as SHA-256
hashes. A session opened before the second step is set up is *pending*: only `/api/auth/*` answers it
(`cbx/security/deps.py`). The screens are `web/mfa.js` (one panel, used by the set-up screen in
`web/app.js` and the Security card in `web/views/settings.js`); the admin side is
`cbx/api/users.py` (`POST /api/users/{id}/mfa-reset`, `expires_at`). Tests: `tests/test_mfa.py`.

Passkeys (Windows Hello, a phone's face or fingerprint) are the next step once the public address is
fixed: WebAuthn ties a passkey to one HTTPS name, and the ngrok address changes.
