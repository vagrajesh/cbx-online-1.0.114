# Going live (pilot)

What "live" means for the pilot: the CBX machine stays on, colleagues on the office network (or on
Tailscale) open `http://<server-name>:8080`, and you hand out accounts. HTTP only, one server, SQLite —
fine for a pilot group (tens of people, not the whole 1 900), not for the internet.

## On the server, once
1. Promote the latest version on **Versions & sandboxes** (the sidebar shows the version top-left).
2. Unzip `server-tools.zip` into the CBX folder (next to `run.bat`), right-click **`go-live.bat`** → *Run as
   administrator*. It opens the firewall for 8080, stops the machine
   sleeping, starts `run.bat` at logon, and schedules `backup.bat` nightly at 02:00.
   The zip also holds `restart.bat` (see below) and a one-page `README.txt`.
3. `.env`: `CBX_MODEL_MODE=live` (after `ollama pull qwen3.8:27b qwen3.5:9b glm-ocr`), `CBX_TESSERACT_CMD`
   if scans matter. Restart `run.bat`.
4. Check from another PC: `http://<server-name>:8080` loads and signs in.

## People
- **Users & roles → Add owner / admin** for your boss and the admins; **Add user** for everyone else, with
  their tools ticked. Each gets a one-time temporary password (copy button) — send it by Teams/phone, not
  the same email as the address. They set their own on first sign-in.
- Start with one team. Add the next once the first is comfortable.
- Locked out (5 wrong tries) → Users & roles → Edit → Unlock. Forgot → they click *Ask the owner*, you see
  *Reset requested* → **Temp password**.

## While the pilot runs
- The `run.bat` window is the server. Minimised is fine; closed is down. After a reboot, log on once.
- Updates: drop the new zip on Versions & sandboxes from anywhere → tests → Promote. Roll back is one
  button. Backups are in `backups\data-YYYY-MM-DD` (last 14).
- What to watch: **Home → Needs attention** (stuck or failed PDF batches, a harvest that stopped, expired
  documents, C-EXA's model half on the CPU or Ollama down, a leftover Node-RED still running, a version that failed its tests —
  one sentence and one button each; the card is hidden when there is nothing). Then the Audit trail
  (denied actions, failed logins) and C-EXA as developer → Bugs & fixes.
- **Home → Getting started** (owner only) ticks itself off as the pilot is set up: first admin, first user,
  a tool granted, model live and 100 % on the GPU, a Harvest source, the nightly backup
  (tick by hand once `go-live.bat` has run) and a first promote. *Hide* puts it away for good.
- C-EXA slow (a minute per answer, a batch of a thousand PDFs taking a day)? Ask her *"why are you slow"*
  or read `docs/cexa-speed.md`: usually the model is partly on the CPU.
- Need to restart the app (after editing `.env`, or it stopped answering)? `server-tools\restart.bat` —
  it closes the app cleanly, waits, forces only if it must, and starts `run.bat` again minimised. What it
  did is in `data\server.log`. Promoting a version restarts by itself; you never need this for that.

## Remote access
- **Tailscale is the supported way for the pilot.** Install it on the server and on your laptop/phone, and
  the app is `http://<server-name>:8080` from anywhere exactly as it is in the office — no ports open to the
  internet, the traffic is encrypted, only devices you signed in are on the network. Add the Tailscale
  name (e.g. `cbx-server.tailnet-name.ts.net`) to `CBX_ALLOWED_HOSTS` in `.env` and run `restart.bat`.
- **Public exposure (ngrok, Cloudflare Tunnel, a port forward) only with all four**: HTTPS end to end; a
  fixed hostname put in `CBX_ALLOWED_HOSTS`; `CBX_SECURE_COOKIES=true` in `.env` (cookies then travel only
  over HTTPS); and an identity gate in front of the app — ngrok's OAuth / Microsoft option or Cloudflare
  Access — so a stranger never reaches the sign-in page. Plain HTTP on the internet is never acceptable:
  passwords and sessions would cross the wire in the clear.
- Node-RED is no longer part of CBX (1.0.48). If it still runs on the server, `server-tools\remove-node-red.bat` (as administrator, once) stops it and removes its start-at-logon task and 1880 firewall rule.

## Don't hand-edit the server
Anything you change by hand under `cbx\` or `web\` on the server is **lost on the next Promote**: the
version replaces those folders wholesale. The owner is told which files were overwritten (the promote
result, the audit trail: "3 hand-edited files were replaced: web/app.js, …") and the edited copies stay in
`data\backups\`, but the fix itself is gone. Make a request instead (New request, or Talk to C-EXA — she can
propose the code change, build and test it in a sandbox, and you promote it). `.env` and `data\` are yours
and are never touched by a version.

## Not yet (say when)
HTTPS + Microsoft sign-in (needs IT: a certificate and an Entra app registration); Postgres before the
wider roll-out; e-mail from C-EXA (needs a mailbox).
