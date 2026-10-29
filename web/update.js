// The update watcher: every open page asks /api/version every half-minute. When the owner has announced
// a version, a bar appears with the switch time and a countdown — the ten minutes people get to save.
// When the timer ends the page saves what is being typed (web/carry.js), waits for the new version to
// come up, and refreshes itself; on the new version the person lands on the same page with the same
// values. A page that only notices afterwards (the tab was asleep) gets a short countdown of its own
// instead of a refresh out of nowhere. While the server restarts the bar says so and keeps polling.
import { api } from './api.js';
import { esc } from './ui.js';
import { carry } from './carry.js';

const POLL_MS = 20000; // idle: an announcement reaches every screen within this
const FAST_MS = 4000; // pending with under two minutes to go, switching, restarting, or new version live
const LATE_GRACE_S = 120; // a page that missed the announcement still gets this long to save
const RELOAD_DELAY_MS = 1500; // after the new version answers: let a final save land

let loaded = ''; // the version this page was served by
let meId = null;
let timer = null;
let tick = null;
let mode = 'idle'; // idle | pending | switching | restarting | live
let deadline = 0; // ms epoch when this page reloads (pending → switch time; live → late grace)
let pendingTag = '';
let bar = null;
let dismissedFor = ''; // the person clicked "Got it" for this tag: the bar shrinks to a pill

const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
const clock = (ms) => new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

function ensureBar() {
  if (bar && bar.isConnected) return bar;
  bar = document.createElement('div');
  bar.id = 'update-bar';
  bar.className = 'update-bar';
  bar.setAttribute('role', 'status');
  const app = document.querySelector('.app');
  (app || document.body).prepend(bar);
  return bar;
}
function hideBar() { if (bar) { bar.remove(); bar = null; } }

function draw() {
  if (mode === 'idle') { hideBar(); return; }
  const b = ensureBar();
  const left = Math.max(0, Math.round((deadline - Date.now()) / 1000));
  const unsaved = carry.isDirty() ? '<span class="dot">Unsaved changes on this page are carried over.</span>' : '';
  const small = dismissedFor === pendingTag && mode === 'pending' && left > 60;
  b.classList.toggle('warm', mode === 'pending' && left <= 60);
  b.classList.toggle('mini', small);
  if (mode === 'pending') {
    b.innerHTML = small
      ? `<b>v${esc(pendingTag)} in ${mmss(left)}</b><button class="lnk" data-more>Details</button>`
      : `<b>Version ${esc(pendingTag)} arrives at ${clock(deadline)}</b><span>in ${mmss(left)} — save your work. This page refreshes itself when the new version is up and brings you back here.</span>${unsaved}<button class="lnk" data-ok>Got it</button>`;
  } else if (mode === 'switching') {
    b.innerHTML = `<b>Switching to version ${esc(pendingTag)}…</b><span>Your work here is kept. The page refreshes as soon as the new version answers.</span>`;
  } else if (mode === 'restarting') {
    b.innerHTML = `<b>The server is restarting.</b><span>Back in a moment — nothing to do.</span>`;
  } else if (mode === 'live') {
    b.innerHTML = `<b>A new version is live.</b><span>This page refreshes in ${mmss(left)} — save your work, or go now.</span>${unsaved}<button class="btn sm primary" data-now>Refresh now</button>`;
  }
  const ok = b.querySelector('[data-ok]'); if (ok) ok.onclick = () => { dismissedFor = pendingTag; draw(); };
  const more = b.querySelector('[data-more]'); if (more) more.onclick = () => { dismissedFor = ''; draw(); };
  const now = b.querySelector('[data-now]'); if (now) now.onclick = () => reloadNow();
}

function reloadNow() {
  carry.snapshot(meId, loaded);
  setTimeout(() => location.reload(), RELOAD_DELAY_MS);
}

function setMode(m) {
  if (m === mode) return;
  mode = m;
  if (tick) { clearInterval(tick); tick = null; }
  if (m === 'pending' || m === 'live') tick = setInterval(onTick, 1000);
  draw();
  schedule();
}

function onTick() {
  if (Date.now() >= deadline) {
    if (mode === 'pending') { carry.snapshot(meId, loaded); setMode('switching'); }
    else if (mode === 'live') reloadNow();
    return;
  }
  draw();
}

async function poll() {
  if (!meId) return;
  let r;
  try { r = await api.get('/api/version', { quiet: true }); }
  catch {
    // the server is away: during a switch that is the restart itself; otherwise say nothing yet
    if (mode === 'switching' || mode === 'restarting') { setMode('restarting'); }
    schedule();
    return;
  }
  if (!meId) return;
  if (r.version && loaded && r.version !== loaded) {
    // the new version answers: a page that was counting down reloads at once, one that only notices
    // now gets the late grace
    if (mode === 'switching' || mode === 'restarting') { reloadNow(); return; }
    if (mode !== 'live') { deadline = Date.now() + LATE_GRACE_S * 1000; pendingTag = r.version; setMode('live'); }
    else draw();
    schedule();
    return;
  }
  if (r.restarting) { setMode('restarting'); schedule(); return; }
  if (r.pending && r.pending.tag) {
    pendingTag = r.pending.tag;
    deadline = Date.now() + (r.pending.seconds_left || 0) * 1000;
    if (r.pending.switching || r.pending.seconds_left <= 0) { if (mode !== 'switching') carry.snapshot(meId, loaded); setMode('switching'); }
    else setMode('pending');
    draw();
  } else if (mode === 'pending' || mode === 'switching') {
    // announced, then called off (or the switch failed) — the bar goes, the audit trail has the story
    setMode('idle');
  } else if (mode === 'restarting') {
    setMode('idle');
  }
  schedule();
}

function schedule() {
  if (timer) clearTimeout(timer);
  const soon = mode === 'switching' || mode === 'restarting' || mode === 'live' || (mode === 'pending' && deadline - Date.now() < 120000);
  timer = setTimeout(poll, soon ? FAST_MS : POLL_MS);
}

export const update = {
  /** Start watching after sign-in. `version` is what /api/auth/me said is running; `id` the signed-in person. */
  start(version, id) {
    loaded = version || '';
    meId = id;
    if (timer) clearTimeout(timer);
    poll();
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('beforeunload', onUnload);
  },
  /** Signed out: stop polling, drop the bar. */
  stop() {
    meId = null;
    if (timer) { clearTimeout(timer); timer = null; }
    if (tick) { clearInterval(tick); tick = null; }
    mode = 'idle';
    hideBar();
    document.removeEventListener('visibilitychange', onVisible);
    window.removeEventListener('beforeunload', onUnload);
  },
};

function onVisible() { if (document.visibilityState === 'visible' && meId) poll(); }
// F5 with something half-typed: keep it, whatever the reason for the reload
function onUnload() { if (meId && carry.isDirty()) carry.snapshot(meId, loaded); }
