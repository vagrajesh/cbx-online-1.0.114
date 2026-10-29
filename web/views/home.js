// Home (1.0.114): CBX-Connect 2's Home, top to bottom.
//   Owner/admin: greeting → Needs you (approvals, the server's checks, what the AI company is waiting on) → Ask (the drawer)
//                → Today (four numbers) → Goals in progress | Happening now → Tools.
//   Everyone else: greeting → Your work (requests, PDF batches, tickets) → Your tools → Your progress (training).
// The 1.0.46 dashboard is still here, behind "Customise Home" (#/home?numbers=1): the tool folder tools/home/ — one page
// per role, Numbers blocks and short lists drawn by the engine's renderer, each person's own arrangement kept in their
// prefs (PUT /api/engine/home/mine). Today's four numbers are the first four of that person's first Numbers block, so
// a customised Home still decides what Today shows.
import { api } from '../api.js';
import { esc, toast, modal, fmtDate } from '../ui.js';
import { lang } from '../lang.js';
import { ICONS } from '../icons.js';
import { renderLayout, applyTheme } from './tool.js';

export default {
  title: 'Home',
  async render(root, ctx) {
    if (ctx.params.numbers) return dashboard(root, ctx);
    const admin = ctx.me.role === 'owner' || ctx.me.role === 'admin';
    return admin ? adminHome(root, ctx) : userHome(root, ctx);
  },
};

// ---- the 1.0.46 dashboard (Customise Home) ------------------------------------------------------------
async function dashboard(root, ctx) {
  {
    const L = lang.get();
    let d;
    try { d = await api.get(`/api/engine/home?lang=${L}`); }
    catch (e) { root.innerHTML = `<div class="head"><div><h1>Home</h1><p>${esc(e.detail || e.message)}</p></div></div>`; return; }
    const me = ctx.me;
    const admin = me.role === 'owner' || me.role === 'admin';
    const S = (k) => (k && d.strings[k]) || k || '';
    let mine = JSON.parse(JSON.stringify(d.mine || {}));
    let editing = false;
    let draft = null; // {tiles, hidden} while customising
    let catalogue = null;

    const hour = new Date().getHours();
    const first = (me.name || '').split(/\s+/)[0] || '';
    const greet = `${hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'}${first ? ', ' + first : ''}`;
    const today = new Date().toLocaleDateString(lang.locale ? lang.locale() : 'en-ZA', { weekday: 'long', day: 'numeric', month: 'long' });

    const state = () => (editing ? draft : mine);
    const tilesFor = (w) => { const t = (state().tiles || {})[w.id]; return Array.isArray(t) ? t : null; };
    const hidden = () => new Set(state().hidden || []);
    const changed = () => JSON.stringify(draft) !== JSON.stringify({ tiles: mine.tiles || {}, hidden: mine.hidden || [] });

    function layoutShown() {
      const no = new Set(Object.entries(d.widgets || {}).filter(([, v]) => v && v.allowed === false).map(([k]) => k));
      const h = editing ? no : new Set([...hidden(), ...no]);
      return { sections: (d.page.layout.sections || []).map((s) => ({ ...s, columns: (s.columns || []).map((c) => ({ ...c, widgets: (c.widgets || []).filter((w) => !h.has(String(w.id))) })) })).filter((s) => s.columns.some((c) => c.widgets.length)) };
    }

    function draw() {
      const custom = (mine.tiles && Object.keys(mine.tiles).length) || (mine.hidden && mine.hidden.length);
      root.innerHTML = `
        <p class="small" style="margin:0 0 10px"><button class="xh-link" id="d-back">← Home</button></p>
        <div class="dash-head"><div><h1>${esc(greet)}</h1><p class="small muted" style="margin:4px 0 0">${esc(today)}${d.page.intro ? ' · ' + esc(S(d.page.intro)) : ''}</p></div>
          <div class="row" style="gap:8px">${editing
            ? `${admin ? '<button class="btn sm ghost" id="d-design" title="Change the starting Home for everyone with this role, in the Builder">Change it for everyone…</button>' : ''}<button class="btn sm" id="d-cancel">Cancel</button>${custom ? '<button class="btn sm ghost" id="d-reset" title="Back to the design for your role">Reset</button>' : ''}<button class="btn sm primary" id="d-save">Save my Home</button>`
            : ctx.viewing ? '' : '<button class="btn sm" id="d-edit">Customise</button>'}</div></div>
        ${d.clock && !editing ? `<div class="thinking small" id="home-clock" style="margin-bottom:14px">${esc(d.clock)}</div>` : ''}
        ${editing ? '<div class="thinking small" style="margin-bottom:14px">Drag a number to move it, × to take it off, + to add one. <b>Hide this block</b> takes a whole block off your Home. Only you see these changes; Reset goes back to the design for your role.</div>' : ''}
        <div id="home-training" hidden></div>
        <div id="dash" class="${editing ? 'dash-edit' : ''}"></div>`;
      const host = root.querySelector('#dash');
      root.dir = d.dir;
      applyTheme(host, d.tool.theme);
      const env = {
        slug: 'home', page: d.page.name, S, strings: d.strings, me, info: d.widgets || {}, editing,
        go: (link) => { if (link && link.route) ctx.go(link.route, link.params || {}); },
        rows: (w, qs) => api.get(`/api/engine/tools/home/data/rows?page=${encodeURIComponent(d.page.name)}&widget=${encodeURIComponent(w.id)}${qs ? '&' + qs : ''}`),
        metrics: (keys) => api.get('/api/engine/metrics?keys=' + encodeURIComponent(keys.join(','))),
        assetUrl: (name) => `/api/engine/tools/home/assets/${encodeURIComponent(name)}`,
        codeUrl: (w) => `/api/engine/tools/home/html/${encodeURIComponent(d.page.name)}/${encodeURIComponent(w.id)}`,
        write: null, tilesFor,
        onTiles: editing ? editTiles : null,
      };
      renderLayout(host, layoutShown(), env);
      if (editing) markBlocks(host);
      wire();
      if (!editing) trainingCard(root, ctx);
    }

    // ---- customising ----------------------------------------------------------------------------
    function start() {
      draft = { tiles: JSON.parse(JSON.stringify(mine.tiles || {})), hidden: [...(mine.hidden || [])] };
      editing = true; draw();
    }
    function keysOf(w) { return tilesFor(w) || (w.items || []).map((i) => i.metric); }
    function setKeys(w, keys) { draft.tiles[w.id] = keys; }

    function editTiles(el, w, shown) {
      const host = el.querySelector('.dash-tiles');
      host.querySelectorAll('.dash-tile').forEach((t) => {
        t.draggable = true;
        t.insertAdjacentHTML('beforeend', `<span class="x" title="Take it off" data-x="${esc(t.dataset.metric)}">×</span>`);
      });
      host.insertAdjacentHTML('beforeend', `<button type="button" class="dash-add" data-add>+ Add a number</button>`);
      host.querySelector('[data-add]').onclick = () => addNumber(w);
      host.querySelectorAll('[data-x]').forEach((x) => (x.onclick = (e) => { e.stopPropagation(); setKeys(w, keysOf(w).filter((k) => k !== x.dataset.x)); draw(); }));
      // drag to reorder, within this block
      let dragKey = null;
      host.querySelectorAll('.dash-tile').forEach((t) => {
        t.addEventListener('dragstart', (e) => { dragKey = t.dataset.metric; t.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', dragKey); });
        t.addEventListener('dragend', () => t.classList.remove('dragging'));
        t.addEventListener('dragover', (e) => { if (dragKey) e.preventDefault(); });
        t.addEventListener('drop', (e) => {
          e.preventDefault();
          const to = t.dataset.metric; if (!dragKey || dragKey === to) return;
          const ks = keysOf(w).filter((k) => shown.includes(k) || k === dragKey);
          const from = ks.indexOf(dragKey); ks.splice(from, 1);
          const at = ks.indexOf(to); const box = t.getBoundingClientRect();
          ks.splice(e.clientX > box.left + box.width / 2 ? at + 1 : at, 0, dragKey);
          setKeys(w, ks); dragKey = null; draw();
        });
      });
    }

    async function addNumber(w) {
      if (!catalogue) { try { catalogue = await api.get('/api/engine/metrics/catalogue'); } catch (e) { toast(e.detail || 'Could not load the numbers.', 'err'); return; } }
      const have = new Set(keysOf(w));
      const groups = catalogue.groups.filter((g) => catalogue.metrics.some((m) => m.group === g));
      const body = document.createElement('div');
      body.innerHTML = groups.map((g) => `<div class="dash-cat-group"><h3>${esc(g)}</h3><div class="dash-cat">${catalogue.metrics.filter((m) => m.group === g).map((m) => `<button type="button" data-k="${esc(m.key)}" ${have.has(m.key) ? 'disabled' : ''}>${esc(m.label)}<span>${esc(m.description)}</span></button>`).join('')}</div></div>`).join('') || '<p class="small muted">No numbers are open to you yet.</p>';
      const m = modal({ title: 'Add a number', body });
      body.querySelectorAll('[data-k]').forEach((b) => (b.onclick = () => { setKeys(w, [...keysOf(w), b.dataset.k]); m.close(); draw(); }));
    }

    function markBlocks(host) {
      const h = hidden();
      host.querySelectorAll('.eng-w').forEach((el) => {
        const id = el.dataset.w; el.classList.add('dash-block'); el.classList.toggle('is-hidden', h.has(id));
        // beside the block, not inside it: a block redraws itself when its data arrives
        el.insertAdjacentHTML('beforebegin', `<div class="hide-row"><button type="button" class="btn sm ghost" data-hide="${esc(id)}">${h.has(id) ? 'Show on my Home' : 'Hide this block'}</button></div>`);
      });
      host.querySelectorAll('[data-hide]').forEach((b) => (b.onclick = () => {
        const id = b.dataset.hide; const s = new Set(draft.hidden);
        if (s.has(id)) s.delete(id); else s.add(id);
        draft.hidden = [...s]; draw();
      }));
    }

    function wire() {
      const q = (id) => root.querySelector(id);
      if (q('#d-back')) q('#d-back').onclick = () => ctx.go('home');
      if (q('#d-edit')) q('#d-edit').onclick = start;
      if (q('#d-design')) q('#d-design').onclick = () => ctx.go('builder', { t: 'home' });
      if (q('#d-cancel')) q('#d-cancel').onclick = () => { editing = false; draft = null; draw(); };
      if (q('#d-save')) q('#d-save').onclick = async () => {
        if (!changed()) { editing = false; draw(); return; }
        try { const r = await api.put('/api/engine/home/mine', draft); mine = r.mine || {}; editing = false; draft = null; toast('Saved — this is your Home now'); draw(); }
        catch (e) { toast(e.detail || 'Could not save.', 'err'); }
      };
      if (q('#d-reset')) q('#d-reset').onclick = async () => {
        try { await api.put('/api/engine/home/mine', { reset: true }); mine = {}; editing = false; draft = null; toast(`Back to the ${S(d.page.title)} design`); draw(); }
        catch (e) { toast(e.detail || 'Could not reset.', 'err'); }
      };
    }

    draw();
  }
}

async function trainingCard(root, ctx) {
  const box = root.querySelector('#home-training');
  if (!box) return;
  let t;
  try { t = await api.get('/api/training', { quiet: true }); } catch { return; }
  if (!box.isConnected || !t.available || t.done >= t.available) return;
  const next = t.lessons.find((l) => l.available && !l.progress.done && l.progress.started && l.progress.step > 0) || t.lessons.find((l) => l.available && !l.progress.done);
  if (!next) return;
  box.hidden = false;
  box.innerHTML = `<div class="card" style="margin-bottom:16px"><div class="bd row between" style="gap:12px;flex-wrap:wrap">
    <div><b>Training</b> <span class="small muted">· ${t.done} of ${t.available} lessons done</span><div class="small muted">${next.progress.started && next.progress.step > 0 ? 'Carry on with' : 'Next up'}: <b>${esc(next.title)}</b> — ${esc(next.blurb)}</div></div>
    <div class="row" style="gap:8px"><button class="btn sm primary" data-go="help">${next.progress.started && next.progress.step > 0 ? 'Continue' : 'Start'}</button></div></div></div>`;
  box.querySelectorAll('[data-go]').forEach((b) => (b.onclick = () => ctx.go(b.dataset.go)));
}

// ---- 1.0.114: the Homes ----------------------------------------------------------------------------------
const greeting = (me) => {
  const hour = new Date().getHours();
  const first = (me.name || '').split(/\s+/)[0] || '';
  return `${hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'}${first ? ', ' + first : ''}`;
};
const todayWord = () => new Date().toLocaleDateString(lang.locale ? lang.locale() : 'en-ZA', { weekday: 'long', day: 'numeric', month: 'long' });
const plural = (n, one, many) => (n === 1 ? one : many.replace('{n}', n));
// a link the server gave (a waiting goal's page): only this app's own addresses are followed
const safeHash = (u) => (typeof u === 'string' && /^#\/[\w\-/?=&%.]*$/.test(u) ? u : '');
const hhmm = (iso) => { if (!iso) return ''; const d = new Date(iso); return Number.isNaN(+d) ? '' : d.toLocaleTimeString(lang.locale ? lang.locale() : 'en-ZA', { hour: '2-digit', minute: '2-digit' }); };

function head(ctx, lead) {
  return `<div class="xh-head"><div><div class="xh-k">${esc(todayWord())}</div><h1 class="xh-h1">${esc(greeting(ctx.me))}</h1><p class="xh-lead" id="xh-lead">${esc(lead)}</p></div>
    <button type="button" class="xh-link" id="xh-custom" title="Your numbers and lists — arrange them your way">Customise Home</button></div>`;
}
const sec = (id, title, extra = '', body = '<div class="xh-empty"><span class="spin"></span></div>') =>
  `<section class="xh-sec" id="${id}"><div class="xh-sec-h"><h2>${esc(title)}</h2>${extra}</div><div class="xh-body">${body}</div></section>`;

function toolTiles(ctx) {
  const tools = ctx.myTools ? ctx.myTools() : [];
  if (!tools.length) return '<div class="xh-empty">No tools yet. The owner or your manager grants them.</div>';
  return `<div class="xh-tiles">${tools.map((t) => `<button type="button" class="xh-tile" data-go="${esc(t.route)}"${t.toolc ? ` style="--toolc:${esc(t.toolc)}"` : ''}><span class="ico">${ICONS[t.icon] || ICONS.tool}</span><b>${esc(t.label)}</b></button>`).join('')}</div>`;
}
function wireGo(root, ctx) {
  root.querySelectorAll('[data-go]').forEach((b) => (b.onclick = () => ctx.go(b.dataset.go)));
  root.querySelectorAll('[data-hash]').forEach((b) => (b.onclick = () => { const h = safeHash(b.dataset.hash); if (h) location.hash = h; }));
  const c = root.querySelector('#xh-custom'); if (c) c.onclick = () => ctx.go('home', { numbers: 1 });
}
const fill = (root, id, html) => { const b = root.querySelector(`#${id} .xh-body`); if (b) b.innerHTML = html; return b; };
const quiet = (p) => api.get(p, { quiet: true }).catch(() => null);

// ---- owner and admins ---------------------------------------------------------------------------------------
async function adminHome(root, ctx) {
  root.innerHTML = `<div class="xh">
    ${head(ctx, '')}
    ${sec('xh-needs', 'Needs you', '<span class="xh-k" id="xh-needs-n"></span>')}
    ${ctx.viewing ? '' : sec('xh-ask', `Ask ${ctx.ai || 'C-EXA'}`, '<span class="xh-k">knows every job, every team</span>', `
      <form class="xh-ask" id="xh-ask-f"><input id="xh-ask-in" autocomplete="off" placeholder="Ask, or tell ${esc(ctx.ai || 'C-EXA')} what you need done…" aria-label="Ask ${esc(ctx.ai || 'C-EXA')}"><button class="btn primary" type="submit">Ask</button></form>
      <div class="xh-qs">${["What's happening today?", 'What changed this week?', 'What is waiting for me?'].map((q) => `<button type="button" class="xh-link" data-q="${esc(q)}">${esc(q)}</button>`).join('')}</div>`)}
    ${sec('xh-today', 'Today')}
    <div class="xh-two">
      ${sec('xh-goals', 'Goals in progress', '<span class="grow"></span><button type="button" class="xh-link" data-go="cexa?tab=now">All goals</button>')}
      ${sec('xh-now', 'Happening now', '<span class="grow"></span><button type="button" class="xh-link" data-go="cexa?tab=now">The office</button>')}
    </div>
    ${sec('xh-tools', 'Tools', '', toolTiles(ctx))}
  </div>`;
  wireGo(root, ctx);
  const f = root.querySelector('#xh-ask-f');
  if (f) {
    const inp = root.querySelector('#xh-ask-in');
    f.onsubmit = (e) => { e.preventDefault(); const q = inp.value.trim(); if (!q) return; inp.value = ''; if (!ctx.ask(q)) toast(`${ctx.ai || 'C-EXA'} is not on this page for you.`, 'err'); };
    root.querySelectorAll('[data-q]').forEach((b) => (b.onclick = () => ctx.ask(b.dataset.q)));
  }

  let alive = true;
  // the teams' live work is the owner's (and whoever was given the teams) — nobody else's page asks for it
  const teams = ctx.me.role === 'owner' || !!ctx.me.can_see_teams;
  const load = async () => {
    const [counts, att, sum, now] = await Promise.all([quiet('/api/home/counts'), quiet('/api/home/attention'), quiet('/api/cexa/goals/summary'), teams ? quiet('/api/cexa/now') : null]);
    if (!alive || !root.isConnected) return;
    needs(root, counts, att, sum);
    goals(root, ctx, sum);
    happening(root, ctx, now, sum);
    wireGo(root, ctx);
  };
  await Promise.all([load(), today(root, ctx)]);
  // the page follows the work while it is open (the menu's counts do the same every minute)
  const t = setInterval(() => { if (document.visibilityState === 'visible') load(); }, 30000);
  return () => { alive = false; clearInterval(t); };
}

function needs(root, counts, att, sum) {
  const rows = [];
  // the AI's questions are listed one by one below (from the company's summary), so they are not counted twice here
  const asks = ((sum && sum.waiting) || []).filter((w) => w.kind === 'ask').length;
  const n = Math.max(0, ((counts && counts.approvals) || 0) - asks);
  if (n) rows.push(`<button type="button" class="xh-row xh-needs" data-go="runs"><span class="t"><b>${esc(plural(n, '1 approval is waiting for you', '{n} approvals are waiting for you'))}</b><span>Flows and agents that stopped to ask you.</span></span><span class="go" aria-hidden="true">→</span></button>`);
  for (const w of (sum && sum.waiting) || []) {
    const h = safeHash(w.url);
    rows.push(`<button type="button" class="xh-row xh-needs info"${h ? ` data-hash="${esc(h)}"` : ' data-go="cexa?tab=now"'}><span class="t"><b>${esc(w.title || '')}</b><span>${esc(w.text || '')}${w.recommend ? ` — <i>${esc(w.recommend)}</i>` : ''}</span></span><span class="go" aria-hidden="true">→</span></button>`);
  }
  for (const it of (att && att.items) || []) {
    const lvl = it.level === 'crit' ? 'crit' : it.level === 'info' ? 'info' : '';
    rows.push(it.go
      ? `<button type="button" class="xh-row xh-needs ${lvl}" data-go="${esc(it.go)}"><span class="t"><b>${esc(it.text)}</b></span><span class="btn sm">${esc(it.label || 'Open')}</span></button>`
      : `<div class="xh-row xh-needs ${lvl}"><span class="t"><b>${esc(it.text)}</b></span></div>`);
  }
  fill(root, 'xh-needs', rows.join('') || '<div class="xh-empty">Nothing. The teams have it.</div>');
  const c = root.querySelector('#xh-needs-n'); if (c) c.textContent = rows.length ? String(rows.length) : '';
  const lead = root.querySelector('#xh-lead');
  if (lead) lead.textContent = rows.length ? plural(rows.length, '1 thing is waiting for you.', '{n} things are waiting for you.') : 'Nothing is waiting for you.';
}

function goals(root, ctx, sum) {
  const box = root.querySelector('#xh-goals');
  if (!box) return;
  if (sum && sum.can_see_company === false) { box.hidden = true; return; }
  const gs = (sum && sum.goals) || [];
  if (!gs.length) { fill(root, 'xh-goals', `<div class="xh-empty">${sum ? 'No goals open. Type one to ' + esc(ctx.ai || 'C-EXA') + ' above, or let the teams find their own.' : 'Goals appear here once the AI company is running.'}</div>`); return; }
  fill(root, 'xh-goals', gs.slice(0, 5).map((g) => {
    const pct = Math.max(0, Math.min(100, +g.pct || (g.total ? Math.round((100 * (g.done || 0)) / g.total) : 0)));
    const word = { planning: 'planning', running: 'running', blocked: 'blocked', waiting_you: 'waiting for you' }[g.status] || g.status || '';
    const tone = g.status === 'waiting_you' ? 'warn' : g.blocked || g.status === 'blocked' ? 'warn' : g.status === 'running' ? 'good' : 'neutral';
    return `<button type="button" class="xh-row xh-goal" data-go="agents?tab=now&goal=${encodeURIComponent(g.id)}"><span class="t"><b>${esc(g.title || '')}</b>
      <span class="meta"><span class="xh-bar${g.blocked ? ' blocked' : ''}"><i style="width:${pct}%"></i></span><span>${esc(`${g.done || 0} of ${g.total || 0}`)}</span>${g.who ? `<span class="who">${esc(g.who)}</span>` : ''}<span class="pill ${tone}">${esc(word)}</span></span>
      ${g.blocked && g.blocker ? `<span class="blk">${esc(g.blocker)}</span>` : ''}</span></button>`;
  }).join(''));
}

function happening(root, ctx, now, sum) {
  const box = root.querySelector('#xh-now');
  if (!box) return;
  if (!now) {
    // not for this admin (the teams are the owner's, or a grant), or not reachable: the goals take the row
    box.hidden = true;
    const two = root.querySelector('.xh-two'); if (two) two.style.gridTemplateColumns = 'minmax(0,1fr)';
    return;
  }
  const rows = [...(now.active || []).map((w) => ({ w, live: true })), ...(now.recent || []).map((w) => ({ w, live: false }))].slice(0, 8);
  fill(root, 'xh-now', rows.length ? rows.map(({ w, live }) => `<div class="xh-row" style="flex-wrap:nowrap;align-items:flex-start"><span class="xh-when">${esc(hhmm(w.updated_at || w.created_at))}</span>
    <span class="t"><b style="font-weight:500">${esc(w.title || '')}</b><span>${esc([w.to, live ? (w.status === 'waiting' ? 'waiting' : 'working') : (w.status || '')].filter(Boolean).join(' · '))}</span></span></div>`).join('')
    : '<div class="xh-empty">Quiet so far.</div>');
}

// Today: the first four numbers of the person's first Numbers block on their dashboard (their own arrangement, else
// their role's design)
async function today(root, ctx) {
  const fallback = ['system.attention', 'flows.waiting_for_me', 'requests.new', 'requests.open'];
  let keys = fallback;
  const d = await quiet(`/api/engine/home?lang=${lang.get()}`);
  if (d && d.page && d.page.layout) {
    const mine = d.mine || {}; const hidden = new Set(mine.hidden || []);
    const ws = (d.page.layout.sections || []).flatMap((s) => (s.columns || []).flatMap((c) => c.widgets || []));
    const w = ws.find((x) => x.type === 'metrics' && !hidden.has(String(x.id)) && !(d.widgets && d.widgets[x.id] && d.widgets[x.id].allowed === false));
    if (w) { const t = (mine.tiles || {})[w.id]; keys = Array.isArray(t) && t.length ? t : (w.items || []).map((i) => i.metric).filter(Boolean); }
  }
  const v = await quiet('/api/engine/metrics?keys=' + encodeURIComponent(keys.slice(0, 12).join(',')));
  if (!root.isConnected) return;
  const got = keys.filter((k) => v && v.values && v.values[k]).slice(0, 4);
  if (!got.length) { fill(root, 'xh-today', '<div class="xh-empty">No numbers to show.</div>'); return; }
  fill(root, 'xh-today', `<div class="xh-stats">${got.map((k) => {
    const m = v.values[k];
    return `<button type="button" class="xh-stat ${m.tone === 'warn' ? 'warn' : ''}"${m.route ? ` data-go="${esc(m.route)}"` : ''}><span class="xh-k">${esc(m.label || k)}</span><span class="v">${m.value == null ? '—' : esc(String(m.value))}</span>${m.sub ? `<span class="d">${esc(m.sub)}</span>` : ''}</button>`;
  }).join('')}</div>`);
  wireGo(root.querySelector('#xh-today'), ctx);
}

// ---- everyone else --------------------------------------------------------------------------------------------
async function userHome(root, ctx) {
  root.innerHTML = `<div class="xh">
    ${head(ctx, 'Your tools are below. Put your work in; you are told when it is done.')}
    ${sec('xh-work', 'Your work')}
    ${sec('xh-tools', 'Your tools', '', toolTiles(ctx))}
    ${sec('xh-prog', 'Your progress', '<span class="grow"></span><button type="button" class="xh-link" data-go="help">All lessons</button>')}
  </div>`;
  wireGo(root, ctx);
  const has = (slug) => (ctx.tools || []).some((t) => t.slug === slug);
  const [reqs, tix, bat, tr] = await Promise.all([quiet('/api/requests/mine'), quiet('/api/support/tickets/mine'), has('pdf-studio') ? quiet('/api/pdfstudio/batches') : null, quiet('/api/training')]);
  if (!root.isConnected) return undefined;
  const rows = [];
  for (const r of (reqs && reqs.requests) || []) rows.push({ at: r.updated_at || r.created_at, kind: 'Request', title: r.title, word: r.status_word || r.status, go: `req-detail?id=${r.id}`, tone: r.status === 'ready' ? 'good' : r.status === 'declined' ? 'crit' : 'neutral' });
  for (const t of (tix && tix.tickets) || []) rows.push({ at: t.updated_at || t.created_at, kind: t.ref || 'Ticket', title: t.title, word: t.word || t.status, go: `tickets?t=${t.id}`, tone: t.status === 'closed' ? 'good' : 'neutral' });
  for (const b of (bat && bat.batches) || []) rows.push({ at: b.finished_at || b.created_at, kind: 'PDF batch', title: b.name || `Batch ${b.id}`, word: b.stalled ? 'stuck' : b.cells_for_user ? `${b.cells_for_user} to check` : b.status, go: `pdf-studio?batch=${b.id}`, tone: b.stalled ? 'warn' : b.cells_for_user ? 'warn' : b.status === 'done' ? 'good' : 'info', live: !b.finished_at && !b.stalled && b.status !== 'done' });
  rows.sort((a, b) => String(b.at || '').localeCompare(String(a.at || '')));
  fill(root, 'xh-work', rows.length ? rows.slice(0, 6).map((r) => `<button type="button" class="xh-row" data-go="${esc(r.go)}"><span class="t"><b>${esc(r.title || '')}</b><span>${esc(r.kind)}${r.at ? ' · ' + esc(fmtDate(r.at, { rel: true })) : ''}</span></span><span class="pill ${r.tone}${r.live ? ' run' : ''}">${esc(r.word || '')}</span></button>`).join('')
    : '<div class="xh-empty">Nothing yet. Open a tool below to start.</div>');
  const live = rows.filter((r) => r.live).length;
  const lead = root.querySelector('#xh-lead');
  if (lead && live) lead.textContent = plural(live, '1 batch is being read. You can close this page; it carries on.', '{n} batches are being read. You can close this page; they carry on.');
  progress(root, tr);
  wireGo(root, ctx);
  return undefined;
}

function progress(root, t) {
  const box = root.querySelector('#xh-prog');
  if (!box) return;
  if (!t || !t.lessons) { box.hidden = true; return; }
  const avail = t.lessons.filter((l) => l.available);
  const done = avail.filter((l) => l.progress && l.progress.done).length;
  const next = avail.find((l) => !l.progress.done && l.progress.started && l.progress.step > 0) || avail.find((l) => !l.progress.done);
  const pct = avail.length ? Math.round((100 * done) / avail.length) : 0;
  fill(root, 'xh-prog', `<div class="xh-prog" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><i style="width:${pct}%"></i></div>
    <div class="xh-row" style="border:0;padding-bottom:0"><span class="t"><b>${esc(`${done} of ${avail.length} lessons done`)}</b><span>${next ? `${next.progress.started && next.progress.step > 0 ? 'Carry on with' : 'Next up'}: ${esc(next.title)} — ${esc(next.blurb || '')}` : 'Every lesson done. Take any of them again from Help.'}</span></span>${next ? `<button type="button" class="btn sm primary" data-go="help">${next.progress.started && next.progress.step > 0 ? 'Continue' : 'Start'}</button>` : ''}</div>`);
}
