// CBX-Connect shell: branding, sign-in, sidebar, hash router, view loading, the AI drawer.
import { api } from './api.js';
import { esc, el, toast, aiAvatar, setAiName, aiInitials } from './ui.js';
import { ICONS } from './icons.js';
import { VIEWS } from './views/index.js';
import { tour } from './training/tour.js';
import { voice } from './voice.js';
import { update } from './update.js';
import { carry } from './carry.js';
import { lang } from './lang.js';
import { mfaPanel } from './mfa.js';
import { startCalls } from './call.js';

// `brand` is what /api/branding returned (has_logo, logo_v …); `ai` is the AI's display name. Views that
// need the name read `state.ai` — the older ones say "C-EXA" literally and are renamed on screen (see AI name).
export const state = { me: null, tools: [], engine: [], product: 'CBX-Connect', org: 'CBRE Excellerate', tagline: 'Digital & Transformation', ai: 'C-EXA', brand: {}, counts: {} };

// ---- branding --------------------------------------------------------------------------
const DEFAULT_ICON = (document.querySelector('link[rel="icon"]') || {}).href || '';

function applyBranding(b) {
  if (!b) return;
  state.brand = b;
  state.product = b.product || state.product;
  state.org = b.org || state.org;
  state.tagline = b.tagline || '';
  state.ai = b.ai_name || 'C-EXA';
  setAiName(state.ai);
  const link = document.querySelector('link[rel="icon"]');
  if (link) {
    if (b.has_favicon) { link.href = `/api/branding/favicon?v=${b.favicon_v || 0}`; link.removeAttribute('type'); }
    else if (DEFAULT_ICON) { link.href = DEFAULT_ICON; }
  }
  // 1.0.114: the owner's accent colours the Classic theme (theme.css reads --brand-accent); a person who picked another
  // theme keeps that theme's own accent
  document.documentElement.style.removeProperty('--accent');
  if (b.accent && /^#[0-9a-fA-F]{6}$/.test(b.accent)) document.documentElement.style.setProperty('--brand-accent', b.accent);
  else document.documentElement.style.removeProperty('--brand-accent');
  if (!state.me) document.title = state.product;
}
async function loadBranding() {
  try { applyBranding(await api.get('/api/branding', { quiet: true })); } catch { /* the defaults above are fine until the server answers */ }
}
// The dark rounded tile. His logo is white lettering on a transparent background — invisible on white —
// so it goes INSIDE the tile; without a logo the tile shows the product's initials.
function textMark() {
  const first = (state.product || 'CBX').split(/[\s\-–—·]/)[0].replace(/[^A-Za-z0-9]/g, '');
  return (first.slice(0, 3) || 'CBX').toUpperCase();
}
// 1.0.114: without an uploaded logo, CBX-Connect 2's wordmark (web/img) sits in the tile instead of the initials
const IMG = new URL('./img/', import.meta.url).href;
export function markHtml(size = '') {
  if (state.brand.has_logo) return `<div class="mark logo ${size}"><img src="/api/branding/logo?v=${state.brand.logo_v || 0}" alt=""></div>`;
  return `<div class="mark x-word ${size}" title="${esc(textMark())}"><img src="${IMG}cbx-wordmark-light.png" alt="${esc(textMark())}"></div>`;
}
// beside the wordmark the product's name without the "CBX" the wordmark already says ("CBX-Connect" → "Connect")
const shortName = () => (state.brand.has_logo ? state.product : (state.product || '').replace(/^CBX[\s\-–—·:]*/i, '') || state.product);
// Settings → Branding saved something: repaint what shows it without a full reload.
window.addEventListener('cbx:branding', (e) => {
  applyBranding(e.detail);
  const brand = root.querySelector('.side .brand');
  if (brand) { brand.innerHTML = brandBlock(); brand.classList.toggle('stacked', !!state.brand.has_logo); }
  const xl = root.querySelector('.x-top .x-logo'); if (xl) xl.innerHTML = xLogo();
  const fab = document.getElementById('cexa-fab'); if (fab) fab.innerHTML = `<span class="av">${esc(aiInitials())}</span>Ask ${esc(state.ai)}`;
  const dh = document.querySelector('#drawer .dh b'); if (dh) dh.textContent = state.ai;
  document.title = ((current && current.view && current.view.title) ? aiText(current.view.title) + ' · ' : '') + state.product;
});
// One line: the mark and the product name. The organisation and the version live at the foot of the
// sidebar and on the sign-in screen — the top of the menu is for getting somewhere.
const brandBlock = () => `${markHtml()}<div><div class="name${state.brand.has_logo ? '' : ' x-sub'}" title="${esc(state.org)}">${esc(shortName())}</div></div>`;
const xLogo = () => `${markHtml()}<span class="x-sub" title="${esc(state.org)}">${esc(shortName())}</span>`;

// Sidebar sections fold. What is folded is remembered in this browser; the section holding the page you
// are on is always open. A folded section still shows the sum of its counts, so nothing hides a number.
const NAV_FOLD_KEY = 'cbx.nav.folded';
function foldedSections() {
  try { const v = JSON.parse(localStorage.getItem(NAV_FOLD_KEY) || '[]'); return new Set(Array.isArray(v) ? v : []); } catch { return new Set(); }
}
function setFolded(set) {
  try { localStorage.setItem(NAV_FOLD_KEY, JSON.stringify([...set])); } catch { /* private window: forget on reload */ }
}

// ---- AI name -----------------------------------------------------------------------------
// The views say "C-EXA" in hundreds of places. When the owner renames the AI, this pass rewrites what people
// SEE — text nodes and title/placeholder/aria-label/alt attributes — and never identifiers: no data-*, href,
// class, id, code, .mono, script or style. It is a no-op while the name is C-EXA, and idempotent otherwise
// (the new name never contains the word C-EXA, so a second pass finds nothing), so it can run on every change.
const AI_WORD = /\bC-EXA\b/g;
const AI_SKIP = new Set(['SCRIPT', 'STYLE', 'CODE', 'PRE', 'SVG']); // never touched at all
const AI_LEAF = new Set(['TEXTAREA', 'INPUT', 'SELECT']); // attributes yes (placeholder, title), contents no
const AI_ATTRS = ['title', 'placeholder', 'aria-label', 'alt'];
const renaming = () => state.ai && state.ai !== 'C-EXA' && !/\bC-EXA\b/.test(state.ai);
export const aiText = (s) => (renaming() ? String(s).replace(AI_WORD, state.ai) : String(s));
function skipEl(n) {
  return AI_SKIP.has(n.tagName) || (n.classList && (n.classList.contains('mono') || n.classList.contains('no-ai-rename')));
}
function renameText(t) {
  const v = t.nodeValue;
  if (!v) return;
  if (v.includes('C-EXA')) t.nodeValue = v.replace(AI_WORD, state.ai);
  else if (v.trim() === 'CE' && aiInitials() !== 'CE') {
    // the two-letter avatar glyph ("CE") beside her messages and on the Users page
    const p = t.parentElement;
    if (p && /(^|\s)(av|avatar)(\s|$)/.test(p.className || '')) t.nodeValue = v.replace('CE', aiInitials());
  }
}
function renameIn(node) {
  if (node.nodeType === Node.TEXT_NODE) { const p = node.parentElement; if (!p || !p.closest('code,pre,script,style,.mono,.no-ai-rename')) renameText(node); return; }
  if (node.nodeType !== Node.ELEMENT_NODE || skipEl(node)) return;
  for (const a of AI_ATTRS) { const v = node.getAttribute(a); if (v && v.includes('C-EXA')) node.setAttribute(a, v.replace(AI_WORD, state.ai)); }
  if (AI_LEAF.has(node.tagName)) return;
  for (const c of node.childNodes) renameIn(c);
}
export function applyAiName(rootEl = document.body) { if (renaming() && rootEl) renameIn(rootEl); }
new MutationObserver((muts) => {
  if (!renaming()) return;
  for (const m of muts) {
    if (m.type === 'characterData') { if (m.target.parentElement && !skipEl(m.target.parentElement)) renameIn(m.target); }
    else if (m.type === 'attributes') { renameIn(m.target); }
    else m.addedNodes.forEach(renameIn);
  }
}).observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: AI_ATTRS });

// ---- navigation ----------------------------------------------------------------------
// section: which sidebar group; roles: who sees it; tool: requires tool access (users and managers)
// 1.0.52: fewer, broader items — pages that belong together are one item (a hub, views/hub.js).
// `hide: true` rows are never drawn; they keep the role gate of an old address that now opens a hub tab.
const NAV = [
  { route: 'home', label: 'Home', icon: 'home' },
  { route: 'help', label: 'Help', icon: 'book' },
  // tools are injected here from state.tools
  { section: 'Workspace', route: 'tickets', label: 'My tickets', icon: 'ticket' },
  { section: 'Workspace', route: 'requests', label: 'My requests', icon: 'list', count: 'my_requests', roles: ['owner', 'admin'] }, // 1.0.66: the owner and admins; everyone else uses Ticket request
  { section: 'Workspace', route: 'mailer', label: 'Mailer', icon: 'mail', roles: ['owner', 'admin'], also: 'support_staff' }, // 1.0.70: every mailbox — support ones and Mine
  { section: 'Workspace', route: 'messages', label: 'Messages', icon: 'chat', count: 'messages' },
  { section: 'Workspace', route: 'runs', label: 'Approvals', icon: 'check-square', count: 'approvals' },
  { section: 'Support', route: 'support', label: 'Dashboard', icon: 'gauge', roles: ['owner', 'admin'], also: 'support_staff' },
  { section: 'Support', route: 'support-tickets', label: 'All tickets', icon: 'headset', roles: ['owner', 'admin'], also: 'support_staff' },
  { section: 'Support', route: 'support-settings', label: 'Support set-up', icon: 'gear', roles: ['owner', 'admin'] },
  { section: 'Admin', route: 'work', label: 'Work', icon: 'inbox', roles: ['owner', 'admin'], count: 'new_requests', countLabel: 'new' },
  { section: 'Admin', route: 'builder', label: 'Builder', icon: 'blocks', roles: ['owner', 'admin'], also: 'can_build' },
  { section: 'Admin', route: 'areas', label: 'Work areas', icon: 'box', roles: ['owner', 'admin'], also: 'can_test' },
  { section: 'Admin', route: 'people', label: 'People & access', icon: 'users', roles: ['owner', 'admin', 'manager'] },
  { section: 'Admin', route: 'audit', label: 'Audit trail', icon: 'clock', roles: ['owner', 'admin', 'manager'] },
  { section: 'Admin', route: 'versions', label: 'Versions', icon: 'box', roles: ['owner'] },
  { section: 'Admin', route: 'cexa', label: 'C-EXA', icon: 'spark', roles: ['owner'], also: 'can_see_teams', count: 'agents_running', countLabel: 'waiting for you' },
  { route: 'req-queue', roles: ['owner', 'admin'], hide: true },
  { route: 'owner-doc', roles: ['owner'], hide: true },
];
// An old address → the hub and tab that now hold it (links, bookmarks, ctx.go calls in the views)
const ALIAS = {
  'new-request': ['requests', 'new'],
  'req-queue': ['work', 'requests'], develop: ['work', 'develop'],
  users: ['people', 'users'], org: ['people', 'org'],
  agents: ['cexa', 'teams'], 'cexa-dev': ['cexa', 'developer'],
  connections: ['settings', 'connections'], warehouse: ['settings', 'warehouse'], mail: ['mailer', ''], mymail: ['mailer', ''],
  training: ['help', ''],
};
const TOOL_ROUTE = { 'pdf-studio': 'pdf-studio', 'master-sheets': 'master-sheets', 'doc-watch': 'doc-watch', 'site-schedules': 'site-schedules', ifrs16: 'ifrs16', ap: 'ap' };
const TOOL_ICON = { 'pdf-studio': 'pdf', 'doc-watch': 'shield', 'site-schedules': 'tower', ifrs16: 'ledger', ap: 'inbox' };
const ROUTE_TOOL = Object.fromEntries(Object.entries(TOOL_ROUTE).map(([slug, r]) => [r, slug]));
// 1.0.114: each tool's colour (CBX-Connect 2's bootstrap.py) — the strip on its page, its Home tile, its menu icon
export const TOOL_COLOR = { ap: '#1F3A5F', 'pdf-studio': '#2E7D57', 'master-sheets': '#2E7D57', ifrs16: '#2E5E7E', 'doc-watch': '#B8731A', 'entity-compliance': '#B8731A', 'site-schedules': '#6B4E9E', 'support-desk': '#A8432F', support: '#A8432F', todo: '#3E7C8C' };
// pages that belong to a tool without being its menu row (the Support pages, PDF Studio's tabs)
const ROUTE_COLOR = { support: 'support', 'support-tickets': 'support', 'support-settings': 'support', tickets: 'support', 'pdf-harvest': 'pdf-studio', 'pdf-profiles': 'pdf-studio', 'master-sheets': 'pdf-studio' };
function pageColor(routeName, view, params) {
  if (routeName === 'tool') return TOOL_COLOR[params.t] || '';
  const gate = view && view.navRoute ? view.navRoute : routeName;
  return TOOL_COLOR[ROUTE_TOOL[gate] || ROUTE_COLOR[gate] || ROUTE_COLOR[routeName] || ''] || '';
}
const toolcAttr = (c) => (c ? ` data-toolc style="--toolc:${c}"` : '');
// A manager is a manager on these pages only (their team's people, audit entries and register). Everywhere
// else the views get them as a plain user — no model names, scores or logs — which is also what the server
// gives them. Views check `ctx.me.role`, so the role they see is decided here, in one place.
const MANAGER_VIEWS = new Set(['users', 'people', 'audit', 'doc-watch', 'todo']);
function viewMe(routeName, view) {
  if (!state.me || state.me.role !== 'manager') return state.me;
  const gate = view && view.navRoute ? view.navRoute : routeName;
  return MANAGER_VIEWS.has(gate) ? state.me : { ...state.me, role: 'user' };
}

// Can the signed-in person open this route? Mirrors the sidebar: role-gated pages from NAV (a view's
// navRoute inherits its parent's gate), tool pages only for people who have that tool. The server
// denies the data anyway; this keeps a typed-in URL from showing a broken page.
function canOpen(routeName, view) {
  let gate = view && view.navRoute ? view.navRoute : routeName;
  const nav = NAV.find((n) => n.route === gate) || NAV.find((n) => ALIAS[gate] && n.route === ALIAS[gate][0]);
  if (nav && nav.roles && !nav.roles.includes(state.me.role) && !(nav.also && state.me[nav.also])) return false;
  const slug = ROUTE_TOOL[gate];
  if (slug && !state.tools.some((t) => t.slug === slug)) return false;
  if (gate === 'tool') { const s = parseHash().params.t; if (!(state.engine || []).some((t) => t.slug === s)) return false; }
  return true;
}
const notAllowed = { title: 'Not for you', async render(el) { el.innerHTML = `<div class="head"><div><h1>That page isn't for you</h1><p>You don't have access to it. Use the menu on the left, or ask the owner if you need it.</p></div></div>`; } };

// old addresses of pages that are tool folders now (1.0.55 To-do, 1.0.56 Site Schedules, 1.0.57 IFRS 16); the built-in pages went in 1.0.79
const REPLACED = new Set(['todo']);  // 1.0.89: Site Schedules is a page again (as CBX-Connect 2); 1.0.92: IFRS 16 too
// built-in menu rows a tool folder now stands for; the built-in page itself stays reachable from the folder
const SHOWN_BY = { 'doc-watch': 'entity-compliance' }; // 1.0.56: Entity Compliance is one menu row, the folder's grid
const folderUp = (slug) => (state.engine || []).some((t) => t.slug === slug && t.ok !== false);
const engineHas = (slug) => REPLACED.has(slug);
function navItems() {
  const items = [NAV[0], NAV[1]]; // Home, Help — then the person's tools
  // Master sheets live inside PDF Studio (1.0.46): no menu row of their own. Someone who holds only
  // master sheets still gets a PDF Studio row, which opens on that tab.
  const hasStudio = state.tools.some((t) => t.slug === 'pdf-studio');
  for (const t of state.tools) {
    if (t.slug === 'master-sheets') {
      if (!hasStudio) items.push({ section: 'My tools', route: 'master-sheets', label: 'PDF Studio', icon: 'pdf', badge: '', count: '', toolc: TOOL_COLOR['pdf-studio'] });
      continue;
    }
    if (engineHas(t.slug)) continue; // a tool folder now; its row comes from the engine
    if (t.slug === 'support-desk') continue; // 1.0.96: a grant, not a page — it opens the Support menu below
    if (SHOWN_BY[t.slug] && folderUp(SHOWN_BY[t.slug])) continue;
    const route = TOOL_ROUTE[t.slug] || t.slug;
    items.push({ section: 'My tools', route, label: t.name, icon: TOOL_ICON[t.slug] || 'tool', badge: t.slug === 'pdf-studio' ? state.ai : '', count: t.slug === 'doc-watch' ? 'docs_due' : '', toolc: TOOL_COLOR[t.slug] || '', slug: t.slug });
  }
  const rest = NAV.slice(2).filter((n) => !n.hide).filter((n) => !n.roles || n.roles.includes(state.me.role) || (n.also && state.me[n.also]));
  // tool folders (the engine): each sits in the section its manifest names, after the built-in rows there
  const eng = (state.engine || []).filter((t) => t.ok !== false);
  const bySec = (sec) => eng.filter((t) => (t.section || 'My tools') === sec).map((t) => ({ section: sec, route: `tool?t=${encodeURIComponent(t.slug)}&p=${encodeURIComponent(t.home || '')}`, label: t.title, icon: t.icon || 'tool', badge: t.badge || '', count: t.slug === 'todo' ? 'todo' : t.slug === 'entity-compliance' ? 'docs_due' : '', engine: true, toolc: TOOL_COLOR[t.slug] || '', slug: t.slug }));
  items.push(...bySec('My tools'));
  let sec = null;
  for (const n of rest) {
    if (n.section !== sec) { if (sec) items.push(...bySec(sec)); sec = n.section; }
    items.push(n);
  }
  if (sec) items.push(...bySec(sec));
  return items;
}
async function loadEngineMenu() {
  try { const m = await api.get('/api/engine/menu?lang=' + encodeURIComponent(lang.get()), { quiet: true }); state.engine = m.tools || []; }
  catch { state.engine = []; }
}

// ---- shell -----------------------------------------------------------------------------
const root = document.getElementById('root');
let current = null; // {route, view, dispose}

function renderShell() {
  const items = navItems().filter((n) => n.route !== 'help'); // Help sits at the foot, beside Settings
  const folded = foldedSections();
  const here = parseHash().route;
  let html = '';
  let sec = null;
  for (const n of items) {
    if (n.section && n.section !== sec) {
      if (sec !== null) html += '</div></div>';
      sec = n.section;
      const inside = items.filter((x) => x.section === sec);
      const open = !folded.has(sec) || inside.some((x) => x.route.split('?')[0] === here);
      const counts = inside.filter((x) => x.count).map((x) => `<span class="count" data-count="${x.count}" data-label=""></span>`).join('');
      html += `<div class="nav-sec ${open ? '' : 'folded'}" data-sec="${esc(sec)}"><button class="sec-hd" aria-expanded="${open}"><span class="eyebrow">${esc(sec)}</span><span class="sum">${counts}</span><span class="chev">${ICONS.chevron || '›'}</span></button><div class="sec-body">`;
    }
    const c = n.count ? `<span class="count" data-count="${n.count}" data-label="${esc(n.countLabel || '')}"></span>` : '';
    const b = n.badge ? `<span class="badge">${esc(n.badge)}</span>` : '';
    html += `<button data-route="${n.route}"${toolcAttr(n.toolc)}><span class="ico">${ICONS[n.icon] || ICONS.tool}</span><span class="lbl" title="${esc(n.label)}">${esc(n.label)}</span>${b}${c}</button>`;
  }
  if (sec !== null) html += '</div></div>';
  const drawer = state.me.role !== 'user';
  root.innerHTML = `
  <div class="app">
    ${topMenu(items, drawer)}
    <aside class="side x-side">
      <div class="brand ${state.brand.has_logo ? 'stacked' : ''}">${brandBlock()}</div>
      ${drawer ? `<button class="ask-btn" id="cexa-fab" title="Ask about what you are looking at"><span class="av">${esc(aiInitials())}</span>Ask ${esc(state.ai)}</button>` : ''}
      <nav class="nav" id="nav">${html}</nav>
      <div class="grow"></div>
      <div class="foot nav">
        <div class="foot-row"><button data-route="help"><span class="ico">${ICONS.book}</span>Help</button><button data-route="settings"><span class="ico">${ICONS.gear}</span>Settings</button></div>
        <div class="user-chip" title="${esc(state.me.name)} · ${esc(state.me.email || '')}"><div class="avatar">${esc(state.me.initials)}</div><div class="who"><b>${esc(state.me.name)}</b><span>${esc(state.me.title || roleLabel(state.me.role) || state.me.team || '')} · v${esc(state.version || '')}</span></div><button class="btn sm ghost logout" id="logout" title="Log out" aria-label="Log out">${ICONS.logout}</button></div>
      </div>
    </aside>
    <div class="side-scrim" id="side-scrim"></div>
    <header class="topbar"><button class="btn sm ghost" id="menu-btn" aria-label="Menu" aria-expanded="false">${ICONS.menu || '☰'}<span>Menu</span></button><span class="tb-name">${esc(state.product)}</span>${drawer ? `<button class="ask-btn sm" id="cexa-fab-top"><span class="av">${esc(aiInitials())}</span>Ask</button>` : ''}</header>
    <main class="main" id="main"></main>
  </div>
  ${state.viewing ? `<div class="viewas-bar" role="status"><span>Viewing as <b>${esc(state.me.name)}</b> · ${esc(roleLabel(state.me.role) || 'User')} — read-only. This is exactly their menu, tools and rows; nothing you click here changes anything.</span><button class="btn sm" id="viewas-stop">Stop viewing</button></div>` : ''}
  ${drawer ? `<aside class="drawer" id="drawer"><div class="dh">${aiAvatar()}<b>${esc(state.ai)}</b><span class="sp"></span><button class="btn sm ghost" id="drawer-clear" title="Clear this conversation">Clear</button><button class="btn sm ghost" id="drawer-close">Close</button></div><div class="ctx" id="drawer-ctx"></div><div class="body" id="drawer-body"></div><div class="compose"><input type="text" id="drawer-in" placeholder="Ask about this page…"><button class="btn primary sm" id="drawer-send">Send</button></div></aside>` : ''}`;
  root.querySelectorAll('[data-route]').forEach((b) => b.addEventListener('click', () => { go(b.dataset.route); setMenu(false); }));
  // 1.0.83: on a phone the menu is a drawer behind the Menu button
  const appEl = root.querySelector('.app'), mb = document.getElementById('menu-btn');
  const setMenu = (on) => { appEl.classList.toggle('menu-open', on); mb.setAttribute('aria-expanded', String(on)); };
  mb.onclick = () => setMenu(!appEl.classList.contains('menu-open'));
  document.getElementById('side-scrim').onclick = () => setMenu(false);
  root.querySelectorAll('.nav-sec .sec-hd').forEach((h) => h.addEventListener('click', () => {
    const sec = h.parentElement; const name = sec.dataset.sec; const f = foldedSections();
    const nowOpen = sec.classList.toggle('folded') === false;
    if (nowOpen) f.delete(name); else f.add(name);
    h.setAttribute('aria-expanded', String(nowOpen));
    setFolded(f);
  }));
  const logout = async () => {
    try { await api.post('/api/auth/logout', undefined, { quiet: true }); } catch { /* signing out locally regardless */ }
    signedOut(); location.hash = ''; renderLogin();
  };
  document.getElementById('logout').onclick = logout;
  document.getElementById('logout-x').onclick = logout;
  // 1.0.114: the top menu's groups open on a click and close on the next one, outside, or Escape
  root.querySelectorAll('.x-dd>.x-navt,.x-dd>.x-me').forEach((b) => b.addEventListener('click', (e) => {
    e.stopPropagation();
    const dd = b.parentElement, open = !dd.classList.contains('open');
    closeMenus();
    dd.classList.toggle('open', open); b.setAttribute('aria-expanded', String(open));
    if (open) { const first = dd.querySelector('.x-mi'); if (first && e.detail === 0) first.focus(); } // opened from the keyboard
  }));
  if (drawer) setupDrawer();
  const vs = document.getElementById('viewas-stop');
  if (vs) vs.onclick = async () => { try { await api.post('/api/view-as/stop', undefined, { quiet: true }); } catch { /* reload regardless */ } location.hash = '#/users'; await boot(); toast('Back to your own view'); };
  if (state.viewing) document.body.classList.add('viewing-as'); else document.body.classList.remove('viewing-as');
  refreshCounts();
  startCounts();
  startCalls(() => state.me && !state.viewing); // C-EXA can ring this person (web/call.js)
}

// 1.0.114: the top menu (Settings → Menu: top). The same rows as the side menu, grouped as dropdowns — My tools,
// Workspace, Support, Admin — with the same count badges (refreshCounts fills every [data-count]). It is always drawn
// and shown by the `menu-top` class, so switching menus in Settings needs no reload. On a phone both menus are the
// side drawer behind the Menu button.
function topMenu(items, drawer) {
  const secs = [];
  for (const n of items) if (n.section && !secs.includes(n.section)) secs.push(n.section);
  const mi = (n) => {
    const c = n.count ? `<span class="count" data-count="${n.count}" data-label="${esc(n.countLabel || '')}"></span>` : '';
    const b = n.badge ? `<span class="badge">${esc(n.badge)}</span>` : '';
    return `<button class="x-mi" role="menuitem" data-route="${n.route}"${toolcAttr(n.toolc)}><span class="ico">${ICONS[n.icon] || ICONS.tool}</span><span class="lbl">${esc(n.label)}</span>${b}${c}</button>`;
  };
  const groups = secs.map((sec) => {
    const inside = items.filter((x) => x.section === sec);
    const keys = [...new Set(inside.filter((x) => x.count).map((x) => x.count))].join(' ');
    return `<div class="x-dd" data-sec="${esc(sec)}"><button class="x-navt" aria-haspopup="true" aria-expanded="false">${esc(sec)}${keys ? `<span class="count" data-sum="${keys}"></span>` : ''}<span class="caret" aria-hidden="true">▾</span></button><div class="x-menu" role="menu">${inside.map(mi).join('')}</div></div>`;
  }).join('');
  const who = esc(state.me.title || roleLabel(state.me.role) || state.me.team || '');
  return `<header class="x-top"><div class="x-top-in">
      <button class="x-logo" data-route="home" aria-label="Home">${xLogo()}</button>
      <button class="x-navt" data-route="home">Home</button>
      ${groups}
      <span class="grow"></span>
      ${drawer ? `<button class="ask-btn" id="cexa-fab-x" title="Ask about what you are looking at"><span class="av">${esc(aiInitials())}</span>Ask ${esc(state.ai)}</button>` : ''}
      <div class="x-dd"><button class="x-me" aria-haspopup="true" aria-expanded="false" title="${esc(state.me.name)} · ${esc(state.me.email || '')}"><span class="avatar">${esc(state.me.initials)}</span><span>${esc((state.me.name || '').split(/\s+/)[0])}</span><span class="caret" aria-hidden="true">▾</span></button>
        <div class="x-menu right" role="menu"><div class="x-who"><b>${esc(state.me.name)}</b><span>${who}${who ? ' · ' : ''}v${esc(state.version || '')}</span></div>
          <button class="x-mi" role="menuitem" data-route="help"><span class="ico">${ICONS.book}</span><span class="lbl">Help</span></button>
          <button class="x-mi" role="menuitem" data-route="settings"><span class="ico">${ICONS.gear}</span><span class="lbl">Settings</span></button>
          <button class="x-mi" role="menuitem" id="logout-x"><span class="ico">${ICONS.logout}</span><span class="lbl">Log out</span></button></div></div>
    </div></header>`;
}
function closeMenus() {
  root.querySelectorAll('.x-dd.open').forEach((d) => { d.classList.remove('open'); const b = d.querySelector(':scope>button'); if (b) b.setAttribute('aria-expanded', 'false'); });
}
document.addEventListener('click', (e) => { if (!e.target.closest || !e.target.closest('.x-dd')) closeMenus(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && root.querySelector('.x-dd.open')) { const b = root.querySelector('.x-dd.open>button'); closeMenus(); if (b) b.focus(); } });

// Stop whatever the current view is doing (polling timers etc.) and forget the session.
function signedOut() {
  disposeCurrent();
  if (tour.active()) tour.stop('signed-out');
  if (countsTimer) { clearInterval(countsTimer); countsTimer = null; }
  update.stop();
  state.me = null; state.tools = []; state.counts = {};
  delete document.body.dataset.role;
  document.querySelectorAll('.modal-bg').forEach((m) => m.remove()); // a dialog must not float over the sign-in screen
}
function disposeCurrent() {
  if (current && current.dispose) { try { current.dispose(); } catch { /* ignore */ } }
  current = null;
}

export const roleLabel = (r) => ({ owner: 'Owner', admin: 'Admin', manager: 'Manager', user: '' }[r] ?? r);

let countsTimer = null;
async function refreshCounts() {
  if (!state.me) return;
  try {
    const c = await api.get('/api/home/counts', { quiet: true });
    if (!state.me) return;
    state.counts = c;
    root.querySelectorAll('[data-count]').forEach((s) => { const v = c[s.dataset.count]; s.textContent = v ? (s.dataset.label ? `${v} ${s.dataset.label}` : v) : ''; });
    // the top menu's groups: one number, everything waiting inside the group
    root.querySelectorAll('[data-sum]').forEach((s) => { const n = s.dataset.sum.split(' ').reduce((a, k) => a + (Number(c[k]) || 0), 0); s.textContent = n ? String(n) : ''; });
    root.querySelectorAll('[data-route="messages"] .count').forEach((m) => { if (c.messages) m.innerHTML = `<span class="dot" style="background:var(--ai);margin:0"></span>`; }); // 1.0.114: both menus
  } catch { /* counts are cosmetic */ }
}
// Sidebar counts follow the data: refreshed on every page change and once a minute while the tab is visible.
function startCounts() {
  if (countsTimer) clearInterval(countsTimer);
  countsTimer = setInterval(() => { if (document.visibilityState === 'visible') refreshCounts(); }, 60000);
}
export { refreshCounts };

// ---- router ----------------------------------------------------------------------------
export function go(route, params = {}) {
  const q = Object.entries(params).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
  location.hash = '#/' + route + (q ? '?' + q : '');
}

function parseHash() {
  const h = location.hash.replace(/^#\/?/, '');
  const i = h.indexOf('?');
  const path = (i < 0 ? h : h.slice(0, i)).replace(/\/+$/, '');
  let params = {};
  try { params = Object.fromEntries(new URLSearchParams(i < 0 ? '' : h.slice(i + 1))); } catch { /* malformed query → no params */ }
  let route = 'home';
  try { route = decodeURIComponent(path) || 'home'; } catch { route = path || 'home'; }
  if (route === 'agents') { if (params.ag && !params.tab) params.tab = params.ag; if (params.job && !params.tab) params.tab = 'jobs'; delete params.ag; } // 1.0.83: Now, Teams, Jobs and Standing agents are C-EXA's own tabs
  if (ALIAS[route]) { const [r, tab] = ALIAS[route]; route = r; if (tab && !params.tab) params.tab = tab; } // pages that moved into a hub
  if ((route === 'doc-watch' || route === 'doc-watch-entity') && folderUp('entity-compliance')) { route = 'tool'; params = { t: 'entity-compliance', p: 'grid' }; } // 1.0.63: Entity Compliance moved into its tool; 1.0.83: the old page is never shown beside it
  if (engineHas(route)) { const slug = route; route = 'tool'; params = { t: slug, p: ((state.engine || []).find((x) => x.slug === slug) || {}).home || '' }; } // an old address opens the tool folder
  return { route, params };
}

const hubOf = (r) => (ALIAS[r] ? ALIAS[r][0] : r);
let nav = 0; // increments per navigation so a slow render can't paint over a newer page
let carried = null; // a snapshot taken before the last refresh, applied once its page is drawn
async function route(keepDrawer = false) {
  if (!state.me) return;
  const { route, params } = parseHash();
  const main = document.getElementById('main');
  if (!main) return;
  const seq = ++nav;
  let view = VIEWS[route] || VIEWS['not-found'];
  if (!canOpen(route, view)) view = notAllowed;
  const here = location.hash.replace(/^#\/?/, '');
  const toolSlug = route === 'tool' ? `tool?t=${encodeURIComponent(params.t || '')}&` : null;
  root.querySelectorAll('[data-route]').forEach((b) => (b.dataset.route === here || (!b.dataset.route.includes('?') && b.dataset.route === hubOf(view.navHighlight || view.navRoute || route)) || (toolSlug && b.dataset.route.startsWith(toolSlug)) ? b.setAttribute('aria-current', 'page') : b.removeAttribute('aria-current')));
  // the page you are on keeps its section open in the sidebar, whatever was folded
  root.querySelectorAll('.nav-sec.folded').forEach((sec) => { if (sec.querySelector('[aria-current="page"]')) { sec.classList.remove('folded'); const h = sec.querySelector('.sec-hd'); if (h) h.setAttribute('aria-expanded', 'true'); } });
  // 1.0.114: in the top menu, the group holding the page is underlined; any open group closes
  closeMenus();
  root.querySelectorAll('.x-top .x-dd[data-sec]').forEach((d) => d.classList.toggle('on', !!d.querySelector('[aria-current="page"]')));
  // the floating "Ask C-EXA" is for every page but C-EXA's own

  disposeCurrent();
  // a dialog belongs to the page it was opened on — browser back/forward must not leave it floating
  document.querySelectorAll('.modal-bg').forEach((m) => m.remove());
  document.title = (view.title ? aiText(view.title) + ' · ' : '') + state.product;
  // Each page renders into its own element. If the person moves on before a slow page has finished,
  // that page keeps writing into a detached element instead of over the new one.
  const host = document.createElement('div');
  host.className = 'page';
  const toolc = pageColor(route, view, params); // 1.0.114: a tool's page carries its colour
  if (toolc) { host.classList.add('has-toolc'); host.style.setProperty('--toolc', toolc); }
  host.innerHTML = '<div class="loading"><span class="spin"></span></div>';
  main.replaceChildren(host);
  window.scrollTo({ top: 0 });
  if (state.me.role !== 'user' && !keepDrawer) drawerContext(route);
  if (seq > 1) refreshCounts(); // page change (the first paint is counted by renderShell)
  const ctx = { me: viewMe(route, view), route, tools: state.tools, params, go, toast, refreshCounts, api, ai: state.ai, product: state.product, viewing: state.viewing,
    // 1.0.114: Home's tool tiles (the person's own menu rows, with their colours) and its Ask box (the drawer)
    myTools: () => navItems().filter((n) => n.section === 'My tools'), toolColor: (slug) => TOOL_COLOR[slug] || '', ask: askInDrawer };
  carry.markClean(); // a new page has nothing unsaved yet
  try {
    const dispose = await view.render(host, ctx);
    if (seq !== nav || !state.me) { if (dispose) { try { dispose(); } catch { /* ignore */ } } return; } // superseded while loading
    current = { route, view, dispose };
    applyAiName(host);
    // back from a refresh (a new version, or F5): the same page, the same half-typed values
    if (carried && carried.hash === (location.hash || '#/home')) {
      const n = carry.apply(carried, host);
      carried = null;
      if (n) toast('Picked up where you were — check what you had typed, then save.');
    } else if (carried) carried = null;
    tour.resume(); // a lesson in progress follows the person from page to page
  } catch (e) {
    if (seq !== nav || !state.me) return;
    console.error(e);
    const denied = e.status === 403, missing = e.status === 404;
    host.innerHTML = `<div class="head"><div><h1>${denied ? "That page isn't for you" : missing ? "That isn't here" : 'Something went wrong'}</h1><p>${esc(e.detail || e.message || e)}</p><p><a href="#/home" style="color:var(--accent)">Back to Home</a></p></div></div>`;
  }
}
window.addEventListener('hashchange', () => route());

// ---- sign-in ---------------------------------------------------------------------------
function renderLogin(forceChange = false) {
  document.title = state.product;
  root.innerHTML = `
  <div class="login" id="login">
    <div class="left"><div class="box">
      <div class="brand" style="padding:0">${markHtml('lg')}<div><div class="name">${esc(state.product)}</div><div class="sub">${esc(state.org)}${state.tagline ? ' · ' + esc(state.tagline) : ''}</div></div></div>
      <form id="f-login" class="${forceChange ? 'hidden' : ''}" style="display:${forceChange ? 'none' : 'flex'};flex-direction:column;gap:16px">
        <div><h1>Sign in</h1><p class="lead">Use the email and password you were given. Access is by invitation only.</p></div>
        <div class="field"><label for="email">Work email</label><input id="email" type="email" autocomplete="username" required></div>
        <div class="field"><label for="pw">Password</label><input id="pw" type="password" autocomplete="current-password" required></div>
        <div class="err-box hidden" id="login-err"></div>
        <button class="btn primary" style="justify-content:center;padding:11px" type="submit">Sign in</button>
        <div class="row between small"><a href="#" id="forgot" style="color:var(--accent)">Forgot password? Ask the owner</a><span class="muted">No self sign-up</span></div>
        <div id="ms-box" style="display:none;flex-direction:column;gap:10px">
          <div class="small muted" style="text-align:center">or, with your company account</div>
          <a class="btn" href="/auth/microsoft" style="justify-content:center;padding:11px;gap:10px"><svg width="18" height="18" viewBox="0 0 21 21" aria-hidden="true"><rect x="1" y="1" width="9" height="9" fill="#F25022"/><rect x="11" y="1" width="9" height="9" fill="#7FBA00"/><rect x="1" y="11" width="9" height="9" fill="#00A4EF"/><rect x="11" y="11" width="9" height="9" fill="#FFB900"/></svg>Sign in with Microsoft</a>
        </div>
      </form>
      <form id="f-code" style="display:none;flex-direction:column;gap:16px">
        <div><h1 id="code-title">Check your email</h1><p class="lead" id="code-lead">This browser is new to me, so I have emailed you a 6-digit code.</p></div>
        <div class="field"><label for="code" id="code-label">6-digit code</label><input id="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="000000" style="font-size:22px;letter-spacing:6px;text-align:center" required></div>
        <p class="small muted" id="code-note">It works once and lasts 10 minutes. After this, I'll remember this browser for 30 days and won't ask again.</p>
        <div class="err-box hidden" id="code-err"></div>
        <button class="btn primary" style="justify-content:center;padding:11px" type="submit">Confirm and sign in</button>
        <div class="row between small"><a href="#" id="code-again" style="color:var(--accent)">Send another code</a><a href="#" id="code-recovery" style="color:var(--accent);display:none">Use a recovery code instead</a><a href="#" id="code-back" class="muted">Back</a></div>
      </form>
      <form id="f-change" style="display:${forceChange ? 'flex' : 'none'};flex-direction:column;gap:14px">
        <div><h1>Set your own password</h1><p class="lead">The password you were given was temporary. Choose a new one before you continue.</p></div>
        <div class="field"><label for="npw">New password</label><input id="npw" type="password" autocomplete="new-password" placeholder="At least 12 characters" required></div>
        <div class="field"><label for="rpw">Repeat it</label><input id="rpw" type="password" autocomplete="new-password" required></div>
        <p class="small muted">12+ characters, checked against leaked-password lists. You'll be signed out after 8 hours idle; 5 wrong attempts lock the account until the owner unlocks it.</p>
        <div class="err-box hidden" id="change-err"></div>
        <button class="btn primary" style="justify-content:center" type="submit">Save and continue</button>
      </form>
    </div></div>
    <div class="right"><div class="q">
      <h2>You ask. It gets done. You get the result.</h2>
      <p>Your tools, your requests and your results — and only yours. Every action, by every person and by ${esc(state.ai)}, is written to an audit trail the owners can see.</p>
      <div class="steps2">
        <div class="st"><i>1</i><div><b>Invitation only.</b> The owners decide who gets in and what they see.</div></div>
        <div class="st"><i>2</i><div><b>Your own password.</b> You replace the temporary one on first sign-in. Nobody else ever sees it.</div></div>
        <div class="st"><i>3</i><div><b>Row-level security.</b> The database refuses rows you weren't granted — not just the screen.</div></div>
      </div>
    </div><span class="badge">argon2 · 8 h idle timeout · append-only audit</span></div>
  </div>`;
  const fl = document.getElementById('f-login'), fc = document.getElementById('f-change'), fk = document.getElementById('f-code');
  // 1.0.97: Sign in with Microsoft — the button when the owner switched it on; the words when Microsoft sign-in was refused
  api.get('/api/auth/microsoft', { quiet: true }).then((m) => { if (m && m.enabled) document.getElementById('ms-box').style.display = 'flex'; }).catch(() => {});
  const MS_WORDS = { off: "Microsoft sign-in isn't switched on yet. Use your email and password.", state: 'That sign-in took too long or came from another window. Try again.',
    denied: "Microsoft didn't sign you in. Try again, or use your email and password.", nouser: 'Your Microsoft account has no CBX-Connect access yet. Ask the owner to add you.',
    blocked: "This account can't sign in right now. Ask the owner.", other: 'This CBX account is linked to a different Microsoft account. Ask the owner.',
    failed: "Microsoft sign-in didn't work. Try again, or use your email and password." };
  const msCode = new URLSearchParams(location.search).get('ms');
  if (msCode && MS_WORDS[msCode]) { const e = document.getElementById('login-err'); e.textContent = MS_WORDS[msCode]; e.classList.remove('hidden'); try { history.replaceState(null, '', location.pathname + location.hash); } catch (x) { /* fine */ } }
  let pendingEmail = '';
  let codeMode = 'email';
  const codeUi = (mode) => {
    // three looks for one form: a code from the inbox, a code from the authenticator app, or a
    // recovery code typed from the sheet the person kept
    codeMode = mode;
    const box = document.getElementById('code'), $ = (id) => document.getElementById(id);
    const app = mode !== 'email';
    $('code-title').textContent = mode === 'recovery' ? 'Use a recovery code' : app ? 'Open your authenticator app' : 'Check your email';
    $('code-label').textContent = mode === 'recovery' ? 'Recovery code' : app ? 'Code from the app' : '6-digit code';
    $('code-note').textContent = mode === 'recovery' ? 'Each recovery code works once. Sign in, then set up a new app under Settings → Security.'
      : app ? 'Codes change every 30 seconds. Three wrong codes lock the account until an owner or admin unlocks it.'
      : "It works once and lasts 10 minutes. After this, I'll remember this browser for 30 days and won't ask again.";
    $('code-again').style.display = app ? 'none' : '';
    $('code-again').textContent = 'Send another code';
    $('code-recovery').style.display = mode === 'totp' ? '' : 'none';
    box.maxLength = mode === 'recovery' ? 9 : 6; box.placeholder = mode === 'recovery' ? 'xxxx-xxxx' : '000000';
    box.inputMode = mode === 'recovery' ? 'text' : 'numeric'; box.autocomplete = mode === 'recovery' ? 'off' : 'one-time-code';
    box.style.letterSpacing = mode === 'recovery' ? '3px' : '6px';
    box.value = ''; box.focus();
  };
  const showCode = (r) => {
    pendingEmail = r.email || fl.email.value.trim();
    document.getElementById('code-lead').textContent = r.message || 'I have emailed you a 6-digit code.';
    fl.style.display = 'none'; fk.style.display = 'flex';
    codeUi(r.method === 'totp' ? 'totp' : 'email');
    if (r.channel === 'terminal') {
      // the mailer failed: the code is in the server's window (and in Users & roles for an admin)
      document.getElementById('code-title').textContent = 'Ask for your code';
      document.getElementById('code-note').textContent = 'The email did not go, so the code is on the server instead. It works once and lasts 10 minutes.';
      document.getElementById('code-again').textContent = 'Make a new code';
    }
    const rec = document.getElementById('code-recovery'); if (r.method === 'totp' && r.recovery === false) rec.style.display = 'none';
  };
  const after = (r) => {
    // where a finished sign-in goes: a temporary password is replaced first, then the second step is
    // set up if this account still lacks one, then the app
    if (r.must_change_password) { fl.style.display = 'none'; fk.style.display = 'none'; fc.style.display = 'flex'; document.getElementById('npw').focus(); }
    else return boot();
  };
  const busy = (form, on) => { const b = form.querySelector('button[type=submit]'); b.disabled = on; b.style.opacity = on ? '.6' : ''; };
  (forceChange ? document.getElementById('npw') : document.getElementById('email')).focus();
  fl.onsubmit = async (e) => {
    e.preventDefault();
    const err = document.getElementById('login-err'); err.classList.add('hidden');
    busy(fl, true);
    try {
      const r = await api.post('/api/auth/login', { email: fl.email.value.trim(), password: fl.pw.value }, { quiet: true });
      if (r.needs_code) showCode(r);
      else await after(r);
    } catch (ex) { err.textContent = ex.detail || 'Sign-in failed.'; err.classList.remove('hidden'); if (ex.status === 401) fl.pw.value = ''; fl.pw.focus(); }
    finally { if (fl.isConnected) busy(fl, false); }
  };
  fk.onsubmit = async (e) => {
    e.preventDefault();
    const err = document.getElementById('code-err'); err.classList.add('hidden');
    busy(fk, true);
    try {
      const r = await api.post('/api/auth/verify', { email: pendingEmail, code: document.getElementById('code').value.trim() }, { quiet: true });
      if (r.recovery_low !== undefined) toast(r.recovery_low ? `Only ${r.recovery_low} recovery code${r.recovery_low === 1 ? '' : 's'} left — make new ones under Settings → Security.` : 'That was your last recovery code. Make new ones under Settings → Security.', 'err');
      await after(r);
    } catch (ex) {
      err.textContent = ex.detail || 'That code did not work.'; err.classList.remove('hidden');
      const box = document.getElementById('code'); box.value = ''; box.focus();
    } finally { if (fk.isConnected) busy(fk, false); }
  };
  document.getElementById('code-again').onclick = async (e) => {
    e.preventDefault();
    // Asking again is just signing in again: the password is still in the form, and the server
    // spends the old code the moment it issues a new one.
    try {
      const r = await api.post('/api/auth/login', { email: pendingEmail, password: fl.pw.value }, { quiet: true });
      if (r.needs_code) { showCode(r); toast('A new code is on its way.'); } else await boot();
    } catch (ex) { toast(ex.detail || 'Could not send another code.', 'err'); }
  };
  document.getElementById('code-recovery').onclick = (e) => { e.preventDefault(); codeUi(codeMode === 'recovery' ? 'totp' : 'recovery'); e.target.textContent = codeMode === 'recovery' ? 'Use the app instead' : 'Use a recovery code instead'; };
  document.getElementById('code-back').onclick = (e) => { e.preventDefault(); fk.style.display = 'none'; fl.style.display = 'flex'; fl.pw.value = ''; fl.email.focus(); };
  fc.onsubmit = async (e) => {
    e.preventDefault();
    const err = document.getElementById('change-err'); err.classList.add('hidden');
    if (fc.npw.value !== fc.rpw.value) { err.textContent = "The two passwords don't match."; err.classList.remove('hidden'); fc.rpw.focus(); return; }
    busy(fc, true);
    try { await api.post('/api/auth/change-password', { new_password: fc.npw.value, repeat: fc.rpw.value }, { quiet: true }); await boot(); }
    catch (ex) {
      if (ex.status === 401) { renderLogin(); toast('Your session ended. Please sign in again.'); return; }
      err.textContent = ex.detail || 'Could not save.'; err.classList.remove('hidden');
    }
    finally { if (fc.isConnected) busy(fc, false); }
  };
  document.getElementById('forgot').onclick = async (e) => {
    e.preventDefault();
    const email = fl.email.value.trim(); if (!email) { toast('Type your email first, then click again.'); fl.email.focus(); return; }
    try { await api.post('/api/auth/forgot', { email }, { quiet: true }); toast('The owner has been asked to issue you a temporary password.'); }
    catch (ex) { toast(ex.detail || "Couldn't send that right now. Try again in a moment.", 'err'); }
  };
}

// ---- two-step verification: the set-up screen ----------------------------------------------
// Shown once, at the first sign-in after 1.0.45 (or a new account): nothing else opens until the
// second step is in place. The same panel is reused from Settings → Security (web/mfa.js).
function renderEnrol(who, mfa) {
  document.title = state.product;
  root.innerHTML = `
  <div class="login" id="login">
    <div class="left"><div class="box" style="max-width:520px">
      <div class="brand" style="padding:0">${markHtml('lg')}<div><div class="name">${esc(state.product)}</div><div class="sub">${esc(state.org)}</div></div></div>
      <div id="enrol"></div>
    </div></div>
    <div class="right"><div class="q">
      <h2>Two steps, every time it matters.</h2>
      <p>Your password is something you know. The app on your phone is something you have. From now on a sign-in from a browser I haven't seen needs both — so a leaked password on its own opens nothing.</p>
      <div class="steps2">
        <div class="st"><i>1</i><div><b>Any authenticator app.</b> Microsoft Authenticator, Google Authenticator, Authy, or the one in your password manager. Nothing from IT is needed.</div></div>
        <div class="st"><i>2</i><div><b>Recovery codes.</b> Ten one-time codes for the day the phone is lost. Keep them somewhere safe — not on the phone.</div></div>
        <div class="st"><i>3</i><div><b>Lost both?</b> An owner or admin resets it and you set it up again. It is written to the audit trail.</div></div>
      </div>
    </div><span class="badge">RFC 6238 · sealed secret · hashed recovery codes</span></div>
  </div>`;
  mfaPanel(document.getElementById('enrol'), { who, mfa, first: true, done: () => boot(), signOut: async () => { try { await api.post('/api/auth/logout', undefined, { quiet: true }); } catch { /* local sign-out anyway */ } renderLogin(); } });
}

// ---- AI drawer ---------------------------------------------------------------------------
// The drawer lives in the shell, so the conversation survives page changes; only the suggestions
// (the top of the body) follow the page. Messages are appended below them. Owners and admins get her
// hands (fixes, reruns); a manager gets answers about their own team only — the server decides that.
function setupDrawer() {
  const d = document.getElementById('drawer'), inp = document.getElementById('drawer-in'), body = document.getElementById('drawer-body');
  body.innerHTML = '<div class="suggs"></div>';
  // voice: a mic that fills the box, and a speaker that reads C-EXA's replies (web/voice.js)
  const compose = d.querySelector('.compose');
  compose.insertBefore(voice.micButton(inp), document.getElementById('drawer-send'));
  compose.append(voice.speakToggle());
  const open = () => { d.classList.add('open'); inp.focus(); };
  const toggle = () => (d.classList.contains('open') ? d.classList.remove('open') : open());
  document.getElementById('cexa-fab').onclick = toggle;
  const top = document.getElementById('cexa-fab-top'); if (top) top.onclick = toggle;
  const topx = document.getElementById('cexa-fab-x'); if (topx) topx.onclick = toggle; // 1.0.114: the top menu's
  document.getElementById('drawer-close').onclick = () => d.classList.remove('open');
  document.getElementById('drawer-clear').onclick = async () => {
    body.querySelectorAll('.msg, .thinking').forEach((m) => m.remove());
    try { await api.post('/api/cexa/thread/clear'); toast('Chat cleared'); } catch (e) { toast(e.detail || 'Could not clear', 'err'); }
    drawerContext();
  };
  // one question to her, shown in the drawer; returns her answer (typing and hands-free talk both use it)
  const askDrawer = async (q) => {
    let said = '';
    const wait = el('<div class="msg ai" style="max-width:100%"><span class="spin"></span></div>');
    body.insertAdjacentHTML('beforeend', `<div class="msg me" style="align-self:flex-end;max-width:90%">${esc(q)}</div>`);
    body.append(wait);
    body.scrollTop = body.scrollHeight;
    const { route: here, params } = parseHash();
    try {
      const r = await api.post('/api/cexa/ask', { question: q, route: here, params });
      const acts = (r.actions || []).length ? `<div class="thinking" style="margin-bottom:6px"><div class="eyebrow">What I did</div>${r.actions.map((a) => `<div><span class="mono" style="color:var(--ai)">${esc(a.tool)}</span> <span class="small muted">${esc(Object.values(a.args || {}).filter(Boolean).join(', '))}</span></div>`).join('')}${r.fix_id ? `<div class="small" style="margin-top:4px">Logged as fix #${r.fix_id} · <a href="#/cexa-dev" style="color:var(--accent)">Bugs &amp; fixes</a></div>` : ''}</div>` : '';
      wait.outerHTML = `${acts}<div class="msg ai" style="max-width:100%">${esc(r.answer || '').replace(/\n/g, '<br>')}</div>`;
      said = r.answer || '';
      if ((r.actions || []).some((a) => (a.tool || '').startsWith('fix_') || a.tool === 'rerun_batch') && parseHash().route === here) route(true);
    }
    catch (e) { said = e.detail || `${state.ai} is not reachable right now.`; wait.outerHTML = `<div class="msg ai" style="max-width:100%">${esc(said)}</div>`; }
    body.scrollTop = body.scrollHeight;
    applyAiName(body);
    return said;
  };
  const send = async () => {
    const q = inp.value.trim(); if (!q) return; inp.value = '';
    voice.speak(await askDrawer(q));
  };
  compose.insertBefore(voice.talkButton(askDrawer), document.getElementById('drawer-send'));
  document.getElementById('drawer-send').onclick = send;
  inp.onkeydown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
    if (e.key === 'Escape') d.classList.remove('open');
  };
}
// 1.0.114: Home's Ask box — open the drawer and send the question there, so the conversation is the drawer's
function askInDrawer(q) {
  const d = document.getElementById('drawer'), inp = document.getElementById('drawer-in'), send = document.getElementById('drawer-send');
  if (!d || !inp || !send) return false;
  d.classList.add('open');
  if (q) { inp.value = q; send.click(); } else inp.focus();
  return true;
}
async function drawerContext(routeName) {
  const ctx = document.getElementById('drawer-ctx'), body = document.getElementById('drawer-body');
  if (!ctx || !body) return;
  const view = VIEWS[routeName];
  ctx.textContent = "You're on: " + aiText(view && view.title ? view.title : routeName);
  const suggs = body.querySelector('.suggs') || body.insertAdjacentElement('afterbegin', el('<div class="suggs"></div>'));
  try {
    const r = await api.get('/api/cexa/suggestions?route=' + encodeURIComponent(routeName), { quiet: true });
    if (parseHash().route !== routeName) return; // moved on before the answer came back
    suggs.innerHTML = (r.suggestions || []).map((s) => `<button class="sugg" data-q="${esc(s.q)}">${esc(s.q)}${s.hint ? `<span>${esc(s.hint)}</span>` : ''}</button>`).join('') || (body.querySelector('.msg') ? '' : '<p class="small muted">Ask me anything about this page.</p>');
    suggs.querySelectorAll('.sugg').forEach((b) => (b.onclick = () => { document.getElementById('drawer-in').value = b.dataset.q; document.getElementById('drawer').classList.add('open'); document.getElementById('drawer-send').click(); }));
    applyAiName(suggs);
  } catch { suggs.innerHTML = ''; }
}

// ---- boot --------------------------------------------------------------------------------
async function boot() {
  try {
    const me = await api.get('/api/auth/me', { quiet: true });
    state.me = me.user; state.tools = me.tools || []; state.version = me.version || ''; state.model = me.model || ''; state.moved = me.moved || [];
    if (me.product) state.product = me.product;
    if (me.org) state.org = me.org;
    if (me.tagline !== undefined) state.tagline = me.tagline || '';
    if (me.ai_name) { state.ai = me.ai_name; setAiName(state.ai); }
    document.body.dataset.role = state.me.role;
    state.viewing = me.viewing_as || null;
    // 1.0.114: the person's theme, menu and text size (Settings → Appearance); viewing as someone shows theirs but
    // does not leave it behind in this browser
    if (window.CBX_LOOK) window.CBX_LOOK.apply(state.me.prefs || {}, !state.viewing);
    if (state.me.must_change_password && !state.viewing) { state.me = null; renderLogin(true); return; }
    if (me.must_enrol_mfa) { const who = state.me; state.me = null; renderEnrol(who, me.mfa || {}); return; }
    disposeCurrent();
    await loadEngineMenu();
    carried = carry.take(state.me.id);
    if (carried && (!location.hash || location.hash === '#/' || location.hash === '#/home') && carried.hash) history.replaceState(null, '', carried.hash);
    renderShell();
    carry.watch(document.getElementById('main'));
    update.start(state.version, state.me.id);
    await route();
  } catch (e) {
    if (e.status === 401) renderLogin();
    else { root.innerHTML = `<div class="loading">Can't reach the server right now. <span class="muted">${esc(e.detail || e.message || '')}</span><br><button class="btn" style="margin-top:14px" id="retry-boot">Try again</button></div>`; const rb = root.querySelector('#retry-boot'); if (rb) rb.onclick = () => location.reload(); }
  }
}
// 1.0.114: Settings saved a new look — the classes change in place; the top and side menus are both drawn already.
window.addEventListener('cbx:look', (e) => { if (window.CBX_LOOK && state.me) { state.me.prefs = { ...(state.me.prefs || {}), ...e.detail }; window.CBX_LOOK.apply(state.me.prefs); closeMenus(); } });
// The person picked another language (Settings): the menu names come back in it and the page redraws.
window.addEventListener('cbx:lang', async () => { if (!state.me) return; await loadEngineMenu(); renderShell(); carry.watch(document.getElementById('main')); route(true); });
// Any non-quiet API call that comes back 401 lands here: the hash is kept, so signing in again
// returns the person to the page they were on.
window.addEventListener('cbx:unauth', () => { if (state.me) { signedOut(); renderLogin(); toast('Your session ended. Please sign in again.'); } });
loadBranding().then(boot);
