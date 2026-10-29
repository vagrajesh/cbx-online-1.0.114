// The tour engine: plays a lesson over the real screens.
//
// A lesson is a list of steps (web/training/lessons.js). Each step points at something on the page,
// says what it is or asks the person to do it, and knows how it is finished: `next` (they read it
// and press Next), `click` (they click the highlighted thing — the tour advances by itself), `wait`
// (they do something and the tour advances when the result appears), or `done` (the last card).
//
// Nothing here is a copy of the app. The spotlight sits over the live page; the button the person
// clicks is the real button; the certificate they file is really filed — against a practice entity
// the lesson opened for them. That is the point: afterwards they have already done it once.
//
// State survives navigation. The app is a hash router that re-renders #main on every route change,
// so the tour lives outside #main (appended to <body>), keeps where it is in sessionStorage, and
// re-finds its target after every hashchange and every 300 ms — pages redraw themselves whenever
// they like, and the spotlight has to follow.
import { api } from '../api.js';
import { esc } from '../ui.js';
import { LESSONS } from './lessons.js';

const KEY = 'cbx_tour';
const PAD = 8;               // spotlight padding around the target
const GAP = 14;              // card distance from the target
const FIND_MS = 10000;       // how long to wait for a target before saying "can't find it"

let root = null;             // the overlay element
let state = null;            // { lesson, step }
let timer = null;            // reposition / wait poll
let clickHandler = null;     // capture-phase listener for `click` steps
let keyHandler = null;
let firstMissingAt = 0;
let lastTargetKey = '';      // so we don't re-render the card every 300 ms
let scrolledFor = '';        // which step we already scrolled the target into view for

const load = () => { try { return JSON.parse(sessionStorage.getItem(KEY) || 'null'); } catch { return null; } };
const save = (s) => { try { s ? sessionStorage.setItem(KEY, JSON.stringify(s)) : sessionStorage.removeItem(KEY); } catch { /* the tour still works for this page */ } };

function route() {
  const h = location.hash.replace(/^#\/?/, '');
  const i = h.indexOf('?');
  const path = (i < 0 ? h : h.slice(0, i)).replace(/\/+$/, '') || 'home';
  let params = {};
  try { params = Object.fromEntries(new URLSearchParams(i < 0 ? '' : h.slice(i + 1))); } catch { /* ignore */ }
  return { path, params };
}

function sameRoute(step) {
  if (!step.route) return true;
  const r = route();
  if (r.path !== step.route) return false;
  for (const [k, v] of Object.entries(step.params || {})) if (String(r.params[k] ?? '') !== String(v)) return false;
  return true;
}

function goTo(step) {
  const q = Object.entries(step.params || {}).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
  location.hash = '#/' + step.route + (q ? '?' + q : '');
}

// Visible target for a selector: the first match that has a box on screen.
function findTarget(sel) {
  if (!sel) return null;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) return el;
  }
  return null;
}

// ---- public -----------------------------------------------------------------------------------
export const tour = {
  active: () => !!state,

  start(lessonId, fromStep = 0) {
    const lesson = LESSONS[lessonId];
    if (!lesson) return false;
    state = { lesson: lessonId, step: Math.max(0, Math.min(fromStep, lesson.steps.length - 1)) };
    save(state);
    mount();
    report();
    show();
    return true;
  },

  // Called by app.js after boot and on every hashchange: pick the tour back up if one is running.
  resume() {
    const s = load();
    if (!s || !LESSONS[s.lesson]) { save(null); return; }
    state = s;
    mount();
    show();
  },

  stop(reason = 'left') {
    if (state && reason !== 'done') report();
    state = null;
    save(null);
    unmount();
    window.dispatchEvent(new CustomEvent('cbx:tour', { detail: { ended: true, reason } }));
  },

};

function lesson() { return state ? LESSONS[state.lesson] : null; }
// A step lives on the page of the nearest earlier step that named one, so a lesson picked up again
// from the Training page (or after a sign-in) knows where to go.
function step() {
  const l = lesson();
  if (!l) return null;
  const s = l.steps[state.step];
  if (!s) return null;
  if (s.route) return s;
  for (let i = state.step - 1; i >= 0; i--) {
    if (l.steps[i].route) return { ...s, route: l.steps[i].route, params: l.steps[i].params, _inherited: true };
  }
  return s;
}

function advance(by) {
  const l = lesson();
  if (!l) return;
  const n = state.step + by;
  if (n >= l.steps.length) { finish(); return; }
  if (n < 0) return;
  state.step = n;
  save(state);
  lastTargetKey = '';
  firstMissingAt = 0;
  report();
  show();
}

function report(done = false) {
  const l = lesson();
  if (!l) return;
  api.post(`/api/training/${state.lesson}`, { step: state.step, total: l.steps.length, done }, { quiet: true }).catch(() => {});
}

function finish() {
  const l = lesson();
  report(true);
  const id = state.lesson;
  state = null;
  save(null);
  renderCard(null, {
    title: l.finish?.title || 'Done',
    text: l.finish?.text || 'You have done it once for real, so next time is just doing it.',
    action: 'done',
    step: l.steps.length, total: l.steps.length, done: true, lessonId: id,
  });
  window.dispatchEvent(new CustomEvent('cbx:tour', { detail: { ended: true, reason: 'done', lesson: id } }));
}

// ---- rendering --------------------------------------------------------------------------------
function mount() {
  if (root) return;
  root = document.createElement('div');
  root.className = 'tour';
  root.innerHTML = `<div class="tour-spot" hidden></div><div class="tour-card" role="dialog" aria-live="polite"></div>`;
  document.body.append(root);
  document.body.classList.add('tour-on');
  timer = setInterval(tick, 300);
  window.addEventListener('resize', tick);
  window.addEventListener('scroll', tick, true);
  keyHandler = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); tour.stop('escape'); }
    else if (e.key === 'ArrowRight' && step() && step().action === 'next' && !e.target.closest('input,textarea,select')) { advance(1); }
  };
  document.addEventListener('keydown', keyHandler);
}

function unmount() {
  if (timer) { clearInterval(timer); timer = null; }
  window.removeEventListener('resize', tick);
  window.removeEventListener('scroll', tick, true);
  if (keyHandler) { document.removeEventListener('keydown', keyHandler); keyHandler = null; }
  detachClick();
  if (root) { root.remove(); root = null; }
  document.body.classList.remove('tour-on');
  lastTargetKey = '';
}

function detachClick() {
  if (clickHandler) { document.removeEventListener('click', clickHandler, true); clickHandler = null; }
}

// Decide what to show for the current step, right now.
function show() {
  const s = step();
  if (!s) { unmount(); return; }
  detachClick();
  if (!sameRoute(s)) {
    // Go there. The hashchange will call resume() → show() again, and the target will be found once the page has drawn.
    renderCard(null, { ...s, title: s.title, text: 'Opening the right page…', action: 'moving' });
    goTo(s);
    return;
  }
  if (s.action === 'click') {
    clickHandler = (ev) => {
      const t = findTarget(s.target);
      if (t && (ev.target === t || t.contains(ev.target))) {
        // Let the click do its job first (open a dialog, navigate), then move on.
        const at = state.step;
        setTimeout(() => { if (state && state.step === at) advance(1); }, s.delay ?? 350);
      }
    };
    document.addEventListener('click', clickHandler, true);
  }
  firstMissingAt = 0;
  tick();
}

function tick() {
  if (!root || !state) return;
  const s = step();
  if (!s) return;
  if (!sameRoute(s)) return; // the hashchange handler will deal with it
  // `wait` steps finish themselves when the result appears
  if (s.action === 'wait' && s.until && findTarget(s.until)) { advance(1); return; }

  const target = findTarget(s.target);
  const spot = root.querySelector('.tour-spot');
  if (s.target && !target) {
    if (!firstMissingAt) firstMissingAt = Date.now();
    spot.hidden = true;
    // An `optional` step is about something not everyone has (the Ask-C-EXA button, a lead-only
    // control): if it is not there after a moment, the lesson simply moves past it.
    if (s.optional && Date.now() - firstMissingAt > 1500) { advance(1); return; }
    if (Date.now() - firstMissingAt > FIND_MS) renderCard(null, { ...s, missing: true });
    else if (lastTargetKey !== 'loading') { renderCard(null, { ...s, text: s.text, loading: true }); lastTargetKey = 'loading'; }
    return;
  }
  firstMissingAt = 0;
  if (target) {
    const r = target.getBoundingClientRect();
    // keep it on screen
    if (r.top < 60 || r.bottom > window.innerHeight - 20) {
      if (scrolledFor !== `${state.lesson}:${state.step}`) { target.scrollIntoView({ block: 'center', behavior: 'smooth' }); scrolledFor = `${state.lesson}:${state.step}`; return; }
    }
    spot.hidden = false;
    spot.style.top = `${r.top - PAD}px`; spot.style.left = `${r.left - PAD}px`;
    spot.style.width = `${r.width + PAD * 2}px`; spot.style.height = `${r.height + PAD * 2}px`;
    spot.classList.toggle('pulse', s.action === 'click');
  } else {
    spot.hidden = true;
  }
  const key = `${state.lesson}:${state.step}:${target ? 'T' : '-'}`;
  if (key !== lastTargetKey) { renderCard(target, s); lastTargetKey = key; }
  else if (target) placeCard(target, s);
}

function renderCard(target, s) {
  if (!root) mount();
  const card = root.querySelector('.tour-card');
  const l = lesson();
  const total = s.total ?? (l ? l.steps.length : 0);
  const idx = s.step ?? (state ? state.step : 0);
  const links = (s.links || []).map((k) => `<a class="btn sm" href="${esc(k.href)}" ${k.download ? 'download' : 'target="_blank" rel="noopener"'}>${esc(k.label)}</a>`).join('');
  let hint = '';
  if (s.missing) hint = `<div class="tour-hint warn">I can't find that on this page. ${s.route ? 'Open the right page and I will carry on.' : 'Try the step again, or skip it.'}</div>`;
  else if (s.loading) hint = `<div class="tour-hint">Waiting for the page…</div>`;
  else if (s.action === 'click') hint = `<div class="tour-hint">Click the highlighted ${esc(s.what || 'button')} to carry on.</div>`;
  else if (s.action === 'wait') hint = `<div class="tour-hint">${esc(s.waitText || 'Do it — the lesson moves on by itself when it is done.')}</div>`;
  const prog = total ? `<div class="tour-prog"><i style="width:${Math.round(((s.done ? total : idx) / total) * 100)}%"></i></div>` : '';
  const buttons = s.action === 'done'
    ? `<button class="btn primary sm" data-t="close">Back to Training</button>`
    : s.action === 'moving'
      ? `<button class="btn sm ghost" data-t="exit">Exit</button>`
      : `${idx > 0 ? '<button class="btn sm ghost" data-t="back">Back</button>' : ''}
         ${s.missing && s.route ? `<button class="btn sm" data-t="go">Open the page</button>` : ''}
         ${s.action === 'next' ? `<button class="btn primary sm" data-t="next">${idx + 1 >= total ? 'Finish' : 'Next'}</button>` : `<button class="btn sm" data-t="next" title="Move on without doing this step">Skip</button>`}
         <button class="btn sm ghost" data-t="exit" title="Leave the lesson (Esc)">Exit</button>`;
  card.innerHTML = `
    <div class="tour-top"><span class="eyebrow">${esc(l ? l.title : 'Training')}</span><span class="small muted">${s.done ? 'Done' : `Step ${idx + 1} of ${total}`}</span></div>
    ${prog}
    <h3>${esc(s.title || '')}</h3>
    <div class="tour-text">${s.html || esc(s.text || '')}</div>
    ${links ? `<div class="row" style="gap:8px;flex-wrap:wrap;margin-top:8px">${links}</div>` : ''}
    ${hint}
    <div class="tour-btns">${buttons}</div>`;
  card.querySelectorAll('[data-t]').forEach((b) => (b.onclick = (e) => {
    e.stopPropagation();
    const t = b.dataset.t;
    if (t === 'next') advance(1);
    else if (t === 'back') advance(-1);
    else if (t === 'exit') tour.stop('exit');
    else if (t === 'go') goTo(s);
    else if (t === 'close') { unmount(); location.hash = '#/training'; }
  }));
  placeCard(target, s);
}

function placeCard(target, s) {
  const card = root && root.querySelector('.tour-card');
  if (!card) return;
  const cw = card.offsetWidth || 360, ch = card.offsetHeight || 200;
  const vw = window.innerWidth, vh = window.innerHeight;
  if (!target) {
    card.style.top = `${Math.max(20, (vh - ch) / 2)}px`;
    card.style.left = `${Math.max(16, (vw - cw) / 2)}px`;
    card.classList.add('centered');
    return;
  }
  card.classList.remove('centered');
  const r = target.getBoundingClientRect();
  const want = s.position || 'auto';
  let top, left;
  const fitsRight = r.right + GAP + cw < vw - 12, fitsLeft = r.left - GAP - cw > 12;
  const fitsBelow = r.bottom + GAP + ch < vh - 12, fitsAbove = r.top - GAP - ch > 12;
  const pos = want !== 'auto' ? want : fitsRight ? 'right' : fitsBelow ? 'bottom' : fitsLeft ? 'left' : fitsAbove ? 'top' : 'bottom';
  if (pos === 'right') { left = r.right + GAP; top = r.top; }
  else if (pos === 'left') { left = r.left - GAP - cw; top = r.top; }
  else if (pos === 'top') { left = r.left; top = r.top - GAP - ch; }
  else { left = r.left; top = r.bottom + GAP; }
  top = Math.min(Math.max(12, top), Math.max(12, vh - ch - 12));
  left = Math.min(Math.max(12, left), Math.max(12, vw - cw - 12));
  card.style.top = `${top}px`; card.style.left = `${left}px`;
}
