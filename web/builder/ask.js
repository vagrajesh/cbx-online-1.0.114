// The Builder's right-hand side: whatever is selected, as plain numbered questions (1.0.53). Nothing
// here asks for a key, a path or a line of code — words go into the tool's English, keys are made
// for them; choices are buttons and lists. Every change is made on B.F (the tool folder) and B.mark()
// redraws the outline and the page.
import { api } from '../api.js';
import { esc, toast, confirmBox } from '../ui.js';
import { OPS, NO_VALUE, WHEN, EVERY, DAYS, DO, newStep, decompile, compile, showTokens, storeTokens } from './rules.js';

export const humanise = (k) => String(k || '').replace(/[_.-]+/g, ' ').replace(/^\w/, (c) => c.toUpperCase()).trim();
export const optSlug = (v) => String(v).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40) || 'x';
export const keyName = (v) => { let s = String(v || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40); if (!/^[a-z]/.test(s)) s = 'x_' + s; return s.slice(0, 40) || 'x'; };
const KEY_RE = /^[a-z][a-z0-9_.]{0,80}$/;

// ---- small form pieces ------------------------------------------------------------------------------
const Q = (n, label, inner, hint = '') => `<div class="bx-q"><div class="bx-ql">${n ? `<i>${n}</i>` : ''}${label}</div>${inner}${hint ? `<div class="bx-hint">${hint}</div>` : ''}</div>`;
const seg = (name, opts, cur) => `<div class="bx-seg" data-seg="${name}">${opts.map(([v, l]) => `<button type="button" data-v="${esc(v)}" class="${String(v) === String(cur) ? 'on' : ''}">${esc(l)}</button>`).join('')}</div>`;
const sel = (name, opts, cur, extra = '') => `<select class="bx-in" data-sel="${name}" ${extra}>${opts.map((o) => { const [v, l] = Array.isArray(o) ? o : [o, o]; return `<option value="${esc(v)}" ${String(v) === String(cur ?? '') ? 'selected' : ''}>${esc(l)}</option>`; }).join('')}</select>`;
const inp = (name, val, extra = '') => `<input class="bx-in" type="text" data-inp="${name}" value="${esc(val ?? '')}" ${extra}>`;
const area = (name, val, h = 80) => `<textarea class="bx-in" data-inp="${name}" style="min-height:${h}px">${esc(val ?? '')}</textarea>`;
const head = (eyebrow, title, extra = '') => `<div class="bx-qhd"><div><div class="eyebrow">${esc(eyebrow)}</div><h2>${esc(title)}</h2></div>${extra}</div>`;
const on = (host, q, ev, fn) => host.querySelectorAll(q).forEach((el) => el.addEventListener(ev, (e) => fn(el, e)));
const segs = (host, fn) => on(host, '[data-seg] button', 'click', (b) => fn(b.parentElement.dataset.seg, b.dataset.v));
const sels = (host, fn) => on(host, '[data-sel]', 'change', (s) => fn(s.dataset.sel, s.value, s));
const inps = (host, fn, ev = 'input') => on(host, '[data-inp]', ev, (i) => fn(i.dataset.inp, i.value, i));

// ---- words ------------------------------------------------------------------------------------------
export function words(B) {
  const en = (B.F.languages.en = B.F.languages.en || {});
  const text = (key) => (key && en[key] !== undefined ? en[key] : key && !KEY_RE.test(key) ? key : '');
  /** Put `value` as the English of obj[field], making the key `fallback` when it has none. */
  const set = (obj, field, fallback, value) => { if (!obj[field] || !KEY_RE.test(obj[field])) obj[field] = fallback; en[obj[field]] = value; };
  const label = (c) => (c.label && en[c.label] !== undefined ? en[c.label] : c.label && !KEY_RE.test(c.label) ? c.label : humanise(c.key));
  return { en, text, set, label };
}

// ---- where a block's rows come from --------------------------------------------------------------------
export const SRC_KINDS = [['data', 'This tool'], ['sheet', 'Warehouse'], ['provider', 'Another tool'], ['api', 'API connection']];
export function srcKind(src) { return !src ? '' : src.grid ? 'grid' : src.sheet ? 'sheet' : src.provider ? 'provider' : src.api ? 'api' : src.data ? 'data' : ''; }
export function sourceItems(B, kind, typ) {
  const W = words(B);
  if (kind === 'data') return Object.entries(B.F.data || {}).map(([n, d]) => ({ id: n, name: W.text(d.title) || humanise(n), sub: `this tool’s own table · ${(d.columns || []).length} columns`, src: { data: n } }));
  if (kind === 'sheet') return (B.ov.sheets || []).map((s) => ({ id: s.slug, name: s.name, sub: `master sheet · ${s.rows.toLocaleString('en-ZA')} rows${s.description ? ' · ' + s.description : ''}`, src: { sheet: s.slug } }));
  if (kind === 'provider') { const want = typ === 'grid' || typ === 'stats' ? 'grid' : 'table'; return (B.ov.providers || []).filter((p) => p.kind === want).map((p) => ({ id: p.name, name: p.title, sub: p.description, src: { provider: p.name } })); }
  if (kind === 'grid') return Object.entries(B.F.pages || {}).flatMap(([pn, pg]) => ((pg.layout || {}).sections || []).flatMap((sec) => (sec.columns || []).flatMap((c) => (c.widgets || []).filter((x) => x.type === 'grid' && (x.source || {}).data).map((x) => ({ id: x.id, name: W.text(x.title) || 'Grid ' + x.id, sub: `the grid on ${W.text(pg.title) || pn}`, src: { grid: x.id } })))));
  if (kind === 'api') return (B.ov.connections || []).map((c) => ({ id: c.slug, name: c.name, sub: c.description || c.base_url, src: { api: c.slug, method: 'GET', path: '', rows: '.', columns: [] } }));
  return [];
}
export function srcColumns(B, src) {
  const W = words(B);
  if (!src) return [];
  if (src.data) return ((B.F.data[src.data] || {}).columns || []).map((c) => ({ key: c.key, label: W.label(c), type: c.type || 'text', options: c.options }));
  if (src.sheet) return (((B.ov.sheets || []).find((s) => s.slug === src.sheet) || {}).columns || []).map((c) => ({ ...c }));
  if (src.provider) return (((B.ov.providers || []).find((p) => p.name === src.provider) || {}).columns || []).map((c) => ({ key: c.key, label: W.en[c.label] || humanise(c.key), type: c.type || 'text' }));
  if (src.api) return (src.columns || []).map((c) => ({ key: c.key, label: W.label(c), type: c.type || 'text' }));
  return [];
}

function whereRows(list, cols, prefix) {
  return list.map((c, i) => {
    const col = cols.find((x) => x.key === c.field) || {};
    const val = NO_VALUE.has(c.op) ? '' : col.type === 'person' ? sel(`${prefix}vs:${i}`, [['{{person.id}}', 'me — the person looking'], ['', '— nobody —']], c.value || '')
      : col.type === 'choice' && (col.options || []).length ? sel(`${prefix}vs:${i}`, [['', '—'], ...col.options.map((o) => [o, o])], c.value)
        : inp(`${prefix}v:${i}`, c.value, `placeholder="${['days_from_today', 'within_days'].includes(c.op) ? 'days' : 'value'}" style="width:110px"`);
    return `<div class="bx-row">${sel(`${prefix}f:${i}`, [['', '— column —'], ...cols.map((x) => [x.key, x.label])], c.field)}${sel(`${prefix}o:${i}`, OPS, c.op || 'equals')}${val}<button type="button" class="bx-x" data-wx="${prefix}:${i}" aria-label="Remove">×</button></div>`;
  }).join('');
}
function wireWhere(host, list, prefix, redraw) {
  sels(host, (k, v) => {
    const vs = k.match(new RegExp(`^${prefix}vs:(\\d+)$`)); if (vs) { list[+vs[1]].value = v; redraw(); return; }
    const m = k.match(new RegExp(`^${prefix}([fo]):(\\d+)$`)); if (!m) return; list[+m[2]][m[1] === 'f' ? 'field' : 'op'] = v; if (m[1] === 'o' && NO_VALUE.has(v)) delete list[+m[2]].value; redraw();
  });
  inps(host, (k, v) => { const m = k.match(new RegExp(`^${prefix}v:(\\d+)$`)); if (m) list[+m[1]].value = v; }, 'change');
  on(host, `[data-wx^="${prefix}:"]`, 'click', (b) => { list.splice(+b.dataset.wx.split(':')[1], 1); redraw(); });
}

// =========================================================================================================
export function renderAsk(host, B) {
  const s = B.sel || {};
  if (s.kind === 'block') return askBlock(host, B, s.id);
  if (s.kind === 'add') return askAdd(host, B, s);
  if (s.kind === 'page') return askPage(host, B);
  if (s.kind === 'data') return askData(host, B, s.id);
  if (s.kind === 'rule') return askRule(host, B, s.id);
  if (s.kind === 'tool') return askTool(host, B, s.what);
  host.innerHTML = head('Builder', 'Click anything to change it') + `<p class="bx-hint">On the left: the tool’s pages, data and rules. In the middle: the page as people see it — click a part of it, or <b>+ add here</b> between parts.</p>`;
}

// ---- a page ---------------------------------------------------------------------------------------------
const LAYOUTS = [['12', 'One column'], ['8,4', 'Wide + narrow'], ['6,6', 'Two halves'], ['4,8', 'Narrow + wide'], ['4,4,4', 'Three']];
function askPage(host, B) {
  const W = words(B), p = B.F.pages[B.page];
  const name = B.page;
  if (!p) { host.innerHTML = head('Page', 'No page yet') + `<p class="bx-hint">Add one on the left.</p>`; return; }
  const roles = (p.access && p.access.roles) || [];
  const secs = p.layout.sections || [];
  host.innerHTML = head('Page', W.text(p.title) || name)
    + Q(1, 'Its name in the tool’s tabs', inp('title', W.text(p.title)))
    + Q(2, 'One line under the name (optional)', inp('intro', W.text(p.intro)))
    + Q(3, 'Who may open it', `<div class="bx-chips">${['owner', 'admin', 'manager', 'user'].map((r) => `<button type="button" data-role="${r}" class="${roles.includes(r) ? 'on' : ''}">${esc(humanise(r))}</button>`).join('')}</div>`, roles.length ? 'Only these roles. Everyone else does not see the page, its tables or its buttons.' : 'None ticked: everyone who may use the tool.')
    + Q(4, 'How it is laid out', secs.map((sec, i) => `<div class="bx-row"><span class="bx-hint" style="min-width:52px">Row ${i + 1}</span>${sel(`lay:${i}`, LAYOUTS, (sec.columns || []).map((c) => c.width || 12).join(','))}${secs.length > 1 ? `<button type="button" class="bx-x" data-secx="${i}" aria-label="Remove row">×</button>` : ''}</div>`).join('') + `<button type="button" class="bx-lnk" data-secadd>+ add a row</button>`, 'A row holds parts side by side. Removing a row keeps its parts: they move up into the row above.')
    + Q(5, 'Width of the page', seg('width', [['narrow', 'Narrow'], ['normal', 'Normal'], ['wide', 'Wide']], (p.style || {}).width || 'normal'))
    + Q(6, 'Shown in the tool’s tabs', seg('menu', [['1', 'Yes'], ['0', 'No (reached by a link)']], p.menu === false ? '0' : '1'))
    + Q(7, 'The first page people see', seg('home', [['1', 'Yes'], ['0', 'No']], B.F.manifest.home === name ? '1' : '0'))
    + `<div class="bx-foot"><button type="button" class="bx-lnk danger" data-delpage>Remove this page</button></div>`;
  inps(host, (k, v) => { if (k === 'title' && p._new) { const old = p.title; const n2 = B.rename('page', name, v); if (n2 !== name) { delete W.en[old]; p.title = ''; W.set(p, 'title', `page.${n2}.title`, v); B.page = n2; } B.mark(); } }, 'change');
  inps(host, (k, v) => { if (k === 'title') W.set(p, 'title', `page.${name}.title`, v); if (k === 'intro') { if (v) W.set(p, 'intro', `page.${name}.intro`, v); else delete p.intro; } B.mark(false); });
  on(host, '[data-role]', 'click', (b) => { const r = b.dataset.role; const cur = new Set(roles); cur.has(r) ? cur.delete(r) : cur.add(r); const list = ['owner', 'admin', 'manager', 'user'].filter((x) => cur.has(x)); if (list.length) p.access = { roles: list }; else delete p.access; B.mark(); });
  segs(host, (k, v) => { if (k === 'width') p.style = { ...(p.style || {}), width: v }; if (k === 'menu') p.menu = v === '1'; if (k === 'home' && v === '1') B.F.manifest.home = name; B.mark(); });
  sels(host, (k, v) => {
    const m = k.match(/^lay:(\d+)$/); if (!m) return;
    const sec = secs[+m[1]], widths = v.split(',').map(Number), old = sec.columns || [];
    const cols = widths.map((w, i) => ({ width: w, widgets: (old[i] || { widgets: [] }).widgets || [] }));
    for (let i = widths.length; i < old.length; i++) cols[cols.length - 1].widgets.push(...(old[i].widgets || []));
    sec.columns = cols; B.mark();
  });
  on(host, '[data-secadd]', 'click', () => { secs.push({ id: 's' + (secs.length + 1) + Date.now().toString(36).slice(-3), columns: [{ width: 12, widgets: [] }] }); B.mark(); });
  on(host, '[data-secx]', 'click', (b) => { const i = +b.dataset.secx; const gone = secs.splice(i, 1)[0]; const into = secs[Math.max(0, i - 1)]; into.columns[into.columns.length - 1].widgets.push(...gone.columns.flatMap((c) => c.widgets || [])); B.mark(); });
  on(host, '[data-delpage]', 'click', async () => { if (!(await confirmBox(`Remove the page “${W.text(p.title) || name}”? Its parts go with it. Tables and rules stay.`, 'Remove'))) return; delete B.F.pages[name]; if (B.F.manifest.home === name) B.F.manifest.home = Object.keys(B.F.pages)[0] || ''; B.page = Object.keys(B.F.pages)[0] || ''; B.select({ kind: 'page' }); });
}

// ---- adding a part ------------------------------------------------------------------------------------
export const ADD = [
  ['Show', [['table', 'Table', 'Rows from this tool, the warehouse or another tool — with search and filters'], ['board', 'Board', 'Cards in columns, one column per pick-list value; drag a card to move it'], ['progress', 'Progress', 'Each goal with a bar: how many rows count towards its target'], ['chart', 'Chart', 'Bars: rows counted or added up, per value of a column'], ['metrics', 'Numbers', 'Big figures the server keeps: open requests, documents due …'], ['grid', 'Compliance grid', 'Companies × document types, one dot each'], ['heading', 'Heading', 'A title line'], ['text', 'Text', 'A paragraph of your words'], ['image', 'Picture', 'A logo or a picture you upload'], ['links', 'Links', 'Buttons or links to pages or websites']]],
  ['Collect', [['form', 'Form', 'Fields people fill in; each send is a row of a table']]],
  ['Do', [['button', 'Button', 'Starts one of the tool’s rules when clicked']]],
  ['Arrange', [['divider', 'Line', 'A line across'], ['spacer', 'Space', 'Some empty space']]],
  ['Advanced', [['embed', 'Website', 'Another website shown inside the page (https)'], ['code', 'Code', 'HTML someone gave you — runs in its own sandbox']]],
];
function askAdd(host, B, s) {
  const W = words(B);
  host.innerHTML = head('Add to the page', 'What goes here?') + ADD.map(([g, items]) => `<div class="bx-group"><h3>${g}</h3><div class="bx-pick">${items.map(([k, n, d]) => `<button type="button" data-add="${k}"><b>${n}</b><span>${d}</span></button>`).join('')}</div></div>`).join('');
  on(host, '[data-add]', 'click', (b) => {
    const t = b.dataset.add, id = `${t}_${Date.now().toString(36).slice(-5)}`;
    const w = { id, type: t };
    const firstData = Object.keys(B.F.data || {})[0];
    if (t === 'heading') { W.set(w, 'text', `w.${id}.text`, 'A heading'); w.level = 2; }
    if (t === 'text') W.set(w, 'text', `w.${id}.text`, 'Your words here.');
    if (t === 'table') { w.source = firstData ? { data: firstData } : (B.ov.sheets || [])[0] ? { sheet: B.ov.sheets[0].slug } : {}; w.search = true; W.set(w, 'title', `w.${id}.title`, 'A table'); }
    if (t === 'chart') { w.source = firstData ? { data: firstData } : (B.ov.sheets || [])[0] ? { sheet: B.ov.sheets[0].slug } : {}; w.group = ''; w.value = { fn: 'count' }; W.set(w, 'title', `w.${id}.title`, 'A chart'); }
    if (t === 'progress') { w.source = firstData ? { data: firstData } : {}; w.count = { data: firstData || '', where: [] }; w.target = ''; W.set(w, 'title', `w.${id}.title`, 'Goals'); }
    if (t === 'board') { w.source = firstData ? { data: firstData } : {}; const ch = firstData ? ((B.F.data[firstData].columns || []).find((c) => c.type === 'choice') || {}).key : ''; w.lane = ch || ''; W.set(w, 'title', `w.${id}.title`, 'A board'); }
    if (t === 'form') { w.source = firstData ? { data: firstData } : {}; W.set(w, 'title', `w.${id}.title`, 'Add one'); W.set(w, 'submit', `w.${id}.submit`, 'Save'); }
    if (t === 'grid') w.source = { provider: 'compliance.grid' };
    if (t === 'metrics') w.items = [];
    if (t === 'button') { const f = Object.entries(B.F.flows || {}).find(([, x]) => (x.trigger || {}).type === 'button'); w.flow = f ? f[0] : ''; W.set(w, 'label', `w.${id}.label`, 'Run'); }
    if (t === 'links') { w.kind = 'buttons'; w.items = []; }
    if (t === 'spacer') w.height = 24;
    if (t === 'embed') { w.url = 'https://'; w.height = 500; }
    if (t === 'code') { w.code = '<p>Hello</p>'; w.height = 240; }
    if (t === 'image') w.asset = '';
    const sec = B.F.pages[B.page].layout.sections[s.sec], col = sec.columns[s.col];
    col.widgets.splice(s.at, 0, w);
    B.select({ kind: 'block', id });
  });
}

// ---- a part of the page ---------------------------------------------------------------------------------
export function findBlock(B, id) {
  const p = B.F.pages[B.page];
  if (!p) return null;
  for (const [si, sec] of (p.layout.sections || []).entries()) for (const [ci, col] of (sec.columns || []).entries()) { const i = (col.widgets || []).findIndex((w) => String(w.id) === String(id)); if (i >= 0) return { w: col.widgets[i], si, ci, i, col, sec }; }
  return null;
}
const TYPE_NAME = { table: 'Table', board: 'Board', progress: 'Progress', chart: 'Chart', metrics: 'Numbers', stats: 'Totals tiles', grid: 'Compliance grid', heading: 'Heading', text: 'Text', image: 'Picture', links: 'Links', form: 'Form', button: 'Button', divider: 'Line', spacer: 'Space', embed: 'Website', code: 'Code' };
export const typeName = (t) => TYPE_NAME[t] || humanise(t);

function askBlock(host, B, id) {
  const W = words(B), at = findBlock(B, id);
  if (!at) { B.select({ kind: 'page' }); return; }
  const w = at.w, t = w.type;
  const move = `<div class="bx-foot"><button type="button" class="bx-lnk" data-mv="-1">↑ Move up</button><button type="button" class="bx-lnk" data-mv="1">↓ Move down</button>${at.sec.columns.length > 1 ? `<button type="button" class="bx-lnk" data-side>→ Other column</button>` : ''}<span style="flex:1"></span><button type="button" class="bx-lnk danger" data-del>Remove</button></div>`;
  const title = (n) => Q(n, 'Title above it (optional)', inp('title', W.text(w.title)));
  let body = '';
  let n = 0;
  const N = () => ++n;
  if (t === 'heading') body = Q(N(), 'The words', inp('text', W.text(w.text))) + Q(N(), 'Size', seg('level', [['1', 'Large'], ['2', 'Medium'], ['3', 'Small']], w.level || 2));
  else if (t === 'text') body = Q(N(), 'The words', area('text', W.text(w.text), 120), '**bold**, *italic*; a line starting with - makes a list; an empty line makes a new paragraph') + Q(N(), 'Tone', seg('tone', [['', 'Normal'], ['muted', 'Quiet']], w.tone || ''));
  else if (t === 'table' || t === 'chart' || t === 'form' || t === 'stats' || t === 'grid' || t === 'board' || t === 'progress') body = askData_(W, w, t, N);
  else if (t === 'metrics') {
    const cat = B.ov.metrics || [], picked = (w.items || []).map((x) => x.metric);
    const groups = [...new Set(cat.map((m) => m.group))];
    body = title(N()) + Q(N(), 'Which numbers', groups.map((g) => `<div class="bx-sub">${esc(g)}</div><div class="bx-chips">${cat.filter((m) => m.group === g).map((m) => `<button type="button" data-metric="${esc(m.key)}" class="${picked.includes(m.key) ? 'on' : ''}" title="${esc(m.description)}">${esc(m.label)}</button>`).join('')}</div>`).join(''), 'Each person sees only the numbers their role and tools allow.');
  } else if (t === 'button') {
    const rules = Object.entries(B.F.flows || {}).filter(([, f]) => (f.trigger || {}).type === 'button');
    body = Q(N(), 'The words on the button', inp('label', W.text(w.label))) + Q(N(), 'What it starts', rules.length ? sel('flow', [['', '— choose a rule —'], ...rules.map(([k, f]) => [k, W.text(f.title) || humanise(k)])], w.flow) : '<span class="bx-hint">No rule starts with a button yet.</span>', `<button type="button" class="bx-lnk" data-newrule>+ a new rule for this button</button>`)
      + Q(N(), 'Ask first', inp('confirm', W.text(w.confirm), 'placeholder="e.g. Send the reminders now? (empty: no question)"')) + Q(N(), 'Colour', seg('primary', [['1', 'Green'], ['0', 'Plain']], w.primary === false ? '0' : '1'));
  } else if (t === 'links') {
    const pages = Object.keys(B.F.pages);
    body = Q(N(), 'Show them as', seg('kind', [['buttons', 'Buttons'], ['links', 'A list of links']], w.kind || 'links')) + Q(N(), 'The links', (w.items || []).map((it, i) => `<div class="bx-row">${inp(`ll:${i}`, W.text(it.label), 'placeholder="Words" style="width:120px"')}${sel(`lg:${i}`, [['', 'goes to …'], ...pages.map((p) => [`page:${p}`, 'Page: ' + (W.text(B.F.pages[p].title) || p)]), ['url', 'A website']], it.route === 'tool' ? `page:${(it.params || {}).p}` : it.url ? 'url' : '')}${it.url !== undefined && it.route !== 'tool' ? inp(`lu:${i}`, it.url, 'placeholder="https://…" style="width:140px"') : ''}<button type="button" class="bx-x" data-lx="${i}">×</button></div>`).join('') + `<button type="button" class="bx-lnk" data-ladd>+ a link</button>`);
  } else if (t === 'image') {
    body = Q(N(), 'The picture', `<div class="bx-row">${sel('asset', [['', '— choose —'], ...(B.assets || []).map((a) => [a.name, a.name])], w.asset)}<button type="button" class="bx-lnk" data-upload>Upload…</button></div>`, 'png, jpg, gif, webp, svg or pdf; 5 MB at most') + Q(N(), 'Words for people who cannot see it', inp('alt', W.text(w.alt))) + Q(N(), 'Caption (optional)', inp('caption', W.text(w.caption))) + Q(N(), 'Place', seg('align', [['left', 'Left'], ['center', 'Centre'], ['right', 'Right']], w.align || 'left'));
  } else if (t === 'spacer') body = Q(N(), 'How much space', seg('height', [['12', 'A little'], ['24', 'Some'], ['48', 'A lot']], String(w.height || 24)));
  else if (t === 'divider') body = '<p class="bx-hint">A line across the column. Nothing to set.</p>';
  else if (t === 'embed') body = Q(N(), 'The website’s address', inp('url', w.url, 'placeholder="https://…"'), 'Some sites refuse to be shown inside another page; then people get a link to open it.') + Q(N(), 'Height', seg('height', [['300', 'Short'], ['500', 'Medium'], ['800', 'Tall']], String(w.height || 500)));
  else if (t === 'code') body = Q(N(), 'The code someone gave you', area('code', w.code, 200), 'It runs in its own sandboxed frame: it cannot read CBX-Connect’s pages, cookies or data.') + Q(N(), 'Height', seg('height', [['240', 'Short'], ['400', 'Medium'], ['700', 'Tall']], String(w.height || 240)));
  const nm = String(W.text(w.title) || W.text(w.text) || W.text(w.label) || typeName(t)).replace(/[*_#`]/g, '').replace(/\s+/g, ' ').trim();
  host.innerHTML = head(typeName(t), nm.length > 48 ? nm.slice(0, 46).trimEnd() + '…' : nm) + body + move;
  wireBlock(host, B, W, w, at);

  function askData_(W, w, t, N) {
    const kind = srcKind(w.source) || (t === 'form' ? 'data' : 'data');
    const kinds = t === 'form' || t === 'board' || t === 'progress' ? [['data', 'This tool']] : t === 'grid' ? [['data', 'This tool'], ['provider', 'Another tool']] : t === 'stats' ? [['grid', 'A grid of this tool'], ['provider', 'Another tool']] : SRC_KINDS;
    const items = sourceItems(B, kind, t);
    const cols = srcColumns(B, w.source);
    const cur = items.find((i) => JSON.stringify(i.src[kind]) === JSON.stringify((w.source || {})[kind]));
    let h = '';
    if (t === 'form' || t === 'table' || t === 'chart') {
      const summary = t === 'table' ? `Show <b>${w.show === 'totals' ? 'the totals' : 'every row'}</b> of <b>${esc(cur ? cur.name : '—')}</b>${(w.where || []).length ? ` where <b>${w.where.map((c) => `${esc((cols.find((x) => x.key === c.field) || {}).label || c.field)} ${esc((OPS.find((o) => o[0] === c.op) || [0, c.op])[1])} ${esc(c.value ?? '')}`).join('</b> and <b>')}</b>` : ''}${w.sort ? `, by <b>${esc((cols.find((x) => x.key === w.sort.key) || {}).label || w.sort.key)}</b>` : ''}.`
        : t === 'chart' ? `Bars of <b>${esc(cur ? cur.name : '—')}</b>: ${(w.value || {}).fn === 'sum' ? `the total of <b>${esc((cols.find((x) => x.key === w.value.of) || {}).label || '…')}</b>` : '<b>how many rows</b>'} per <b>${esc((cols.find((x) => x.key === w.group) || {}).label || '…')}</b>.`
          : `Each send adds a row to <b>${esc(cur ? cur.name : '—')}</b>.`;
      h += `<div class="bx-sentence">${summary}</div>`;
    }
    h += Q(N(), t === 'form' ? 'Which table it fills' : 'Take the data from', kinds.length > 1 ? seg('srckind', kinds, kind) : '')
      + (items.length ? `<div class="bx-pick">${items.map((i) => `<button type="button" data-src="${esc(i.id)}" class="${cur && cur.id === i.id ? 'on' : ''}"><b>${esc(i.name)}</b><span>${esc(i.sub || '')}</span></button>`).join('')}</div>` : `<p class="bx-hint">${kind === 'data' ? 'This tool has no table yet — add one under Data on the left.' : kind === 'sheet' ? 'The warehouse has no master sheets yet (PDF Studio makes them).' : kind === 'api' ? 'No API connections yet (Settings → API connections).' : 'Nothing here.'}</p>`);
    if (kind === 'api' && w.source && w.source.api) h += Q('', 'The connection’s details', `<div class="bx-row">${sel('api_method', ['GET', 'POST'], w.source.method || 'GET')}${inp('api_path', w.source.path, 'placeholder="/items?status=open" style="flex:1"')}</div><div class="bx-row">Where the list is ${inp('api_rows', w.source.rows || '.', 'style="width:120px"')}</div><div class="bx-row">Fields ${inp('api_cols', (w.source.columns || []).map((c) => c.key).join(', '), 'placeholder="name, total, due" style="flex:1"')}</div>`, 'Ask whoever set up the connection for these three.');
    if (t === 'table') {
      const shown = (w.columns && w.columns.length ? w.columns.map((c) => c.key) : cols.map((c) => c.key));
      h += Q(N(), 'Show it as', seg('show', [['rows', 'Every row'], ['totals', 'Totals only']], w.show || 'rows'));
      if (w.show === 'totals') h += Q(N(), 'Add up', `<div class="bx-chips">${cols.filter((c) => ['int', 'number'].includes(c.type)).map((c) => `<button type="button" data-total="${esc(c.key)}" class="${(w.totals || []).includes(c.key) ? 'on' : ''}">${esc(c.label)}</button>`).join('') || '<span class="bx-hint">No number columns — the rows are counted.</span>'}</div>`);
      else h += Q(N(), 'Columns', `<div class="bx-chips">${cols.map((c) => `<button type="button" data-col="${esc(c.key)}" class="${shown.includes(c.key) ? 'on' : ''}">${esc(c.label)}</button>`).join('')}</div>`, 'Click to show or hide. They show in the order you pick them.');
      h += Q(N(), 'Only rows where', whereRows(w.where || [], cols, 'w') + `<button type="button" class="bx-lnk" data-wadd>+ ${(w.where || []).length ? 'and' : 'add a condition'}</button>`, (w.where || []).length ? '' : 'No condition: every row shows.');
      if (w.show !== 'totals') {
        h += Q(N(), 'Sorted by', `<div class="bx-row">${sel('sortkey', [['', 'Newest first'], ...cols.map((c) => [c.key, c.label])], (w.sort || {}).key)}${w.sort ? seg('sortdir', [['0', 'A → Z / low first'], ['1', 'Z → A / high first']], w.sort.desc ? '1' : '0') : ''}</div>`);
        const filt = kind === 'provider' ? ((B.ov.providers.find((p) => p.name === (w.source || {}).provider) || {}).filters || []).map((f) => ({ key: f.key, label: W.en[f.label] || humanise(f.key) })) : cols.filter((c) => ['choice', 'link', 'bool'].includes(c.type));
        if (filt.length) h += Q(N(), 'Pick-lists people can filter by', `<div class="bx-chips">${filt.map((c) => `<button type="button" data-filter="${esc(c.key)}" class="${(w.filters || []).includes(c.key) ? 'on' : ''}">${esc(c.label)}</button>`).join('')}</div>`);
        h += Q(N(), 'A search box', seg('search', [['1', 'Yes'], ['0', 'No']], w.search === false ? '0' : '1'));
        if (kind === 'data') {
          const rules = Object.entries(B.F.flows || {}).filter(([, f]) => (f.trigger || {}).type === 'button');
          h += actionsQ(N(), w, rules, false, W) + presetQ(N(), w, cols);
        }
        if (kind === 'provider') h += Q(N(), 'Clicking a row', seg('row_link', [['1', 'Opens it'], ['0', 'Nothing']], w.row_link ? '1' : '0'));
      }
      h += title(N());
    } else if (t === 'chart') {
      h += Q(N(), 'One bar per', sel('group', [['', '— choose a column —'], ...cols.map((c) => [c.key, c.label])], w.group))
        + Q(N(), 'How tall each bar is', `<div class="bx-row">${seg('vfn', [['count', 'How many rows'], ['sum', 'The total of …']], (w.value || {}).fn || 'count')}${(w.value || {}).fn === 'sum' ? sel('vof', [['', '— a number column —'], ...cols.filter((c) => ['int', 'number'].includes(c.type)).map((c) => [c.key, c.label])], w.value.of) : ''}</div>`)
        + Q(N(), 'Only rows where', whereRows(w.where || [], cols, 'w') + `<button type="button" class="bx-lnk" data-wadd>+ ${(w.where || []).length ? 'and' : 'add a condition'}</button>`)
        + title(N());
    } else if (t === 'form') {
      const shown = (w.fields && w.fields.length ? w.fields.map((c) => c.key) : cols.map((c) => c.key));
      h += Q(N(), 'The fields', `<div class="bx-chips">${cols.map((c) => `<button type="button" data-field="${esc(c.key)}" class="${shown.includes(c.key) ? 'on' : ''}">${esc(c.label)}</button>`).join('')}</div>`) + Q(N(), 'The words on its button', inp('submit', W.text(w.submit) || 'Save')) + presetQ(N(), w, cols) + title(N());
    } else if (t === 'progress') {
      const cnt = w.count || (w.count = { data: '', where: [] });
      const ccols = srcColumns(B, { data: cnt.data });
      const numCols = cols.filter((c) => ['int', 'number', 'calc'].includes(c.type));
      const goalVals = [['', '— a value —'], ['{{person.id}}', 'me — the person looking'], ...cols.map((c) => [`{{row.${c.key}}}`, 'the goal’s ' + c.label])];
      const shown = (w.fields || []).map((f) => (typeof f === 'string' ? f : f.key));
      h += Q(N(), 'Only the goals where', whereRows(w.where || [], cols, 'w') + `<button type="button" class="bx-lnk" data-wadd>+ ${(w.where || []).length ? 'and' : 'a condition'}</button>`, (w.where || []).length ? '' : 'Every goal shows.')
        + Q(N(), 'The name of each goal', sel('plabel', [['', '— first words column —'], ...cols.filter((c) => (c.type || 'text') === 'text').map((c) => [c.key, c.label])], w.label))
        + Q(N(), 'Its target', numCols.length ? sel('ptarget', [['', '— a number column —'], ...numCols.map((c) => [c.key, c.label])], w.target) : '<span class="bx-hint">The goals table needs a number column for the target.</span>')
        + Q(N(), 'What counts towards it', `<div class="bx-row">rows of ${sel('pdata', [['', '— a table —'], ...Object.keys(B.F.data).map((x) => [x, W.text(B.F.data[x].title) || humanise(x)])], cnt.data)}</div>`
          + (cnt.where || []).map((c, i) => `<div class="bx-grp"><div class="bx-row">${sel(`pcf:${i}`, [['', '— column —'], ...ccols.map((x) => [x.key, x.label])], c.field)}${sel(`pco:${i}`, OPS, c.op || 'equals')}${NO_VALUE.has(c.op) ? '' : sel(`pcv:${i}`, goalVals.concat(goalVals.some(([v]) => v === c.value) || !c.value ? [] : [[c.value, c.value]]), c.value || '')}<button type="button" class="bx-x" data-pcx="${i}">×</button></div>${NO_VALUE.has(c.op) ? '' : `<label class="bx-check"><input type="checkbox" data-pskip="${i}" ${c.skip_empty ? 'checked' : ''}> only when the goal has a value there (empty = any)</label>`}</div>`).join('')
          + `<button type="button" class="bx-lnk" data-pcadd>+ ${(cnt.where || []).length ? 'and' : 'a condition'}</button>`, 'e.g. Status is Done · Finished is at least the goal’s From · Finished is at most the goal’s To · Size is the goal’s Size.')
        + Q(N(), 'Also show', `<div class="bx-chips">${cols.filter((c) => c.key !== w.label && c.key !== w.target).map((c) => `<button type="button" data-bf="${esc(c.key)}" class="${shown.includes(c.key) ? 'on' : ''}">${esc(c.label)}</button>`).join('')}</div>`)
        + actionsQ(N(), w, Object.entries(B.F.flows || {}).filter(([, f]) => (f.trigger || {}).type === 'button'), true, W)
        + title(N());
    } else if (t === 'board') {
      const choiceCols = cols.filter((c) => c.type === 'choice');
      const shown = (w.fields || []).map((f) => (typeof f === 'string' ? f : f.key));
      h += Q(N(), 'Its columns come from', choiceCols.length ? sel('lane', [['', '— a pick-list column —'], ...choiceCols.map((c) => [c.key, c.label])], w.lane) : '<span class="bx-hint">The table needs a pick-list column (e.g. Status) first.</span>', 'One column per option; dragging a card to another column changes the value.')
        + Q(N(), 'The words on each card', sel('card', [['', '— first words column —'], ...cols.filter((c) => ['text', 'choice'].includes(c.type || 'text')).map((c) => [c.key, c.label])], w.card))
        + Q(N(), 'Also on each card', `<div class="bx-chips">${cols.filter((c) => c.key !== w.lane && c.key !== w.card).map((c) => `<button type="button" data-bf="${esc(c.key)}" class="${shown.includes(c.key) ? 'on' : ''}">${esc(c.label)}</button>`).join('')}</div>`)
        + Q(N(), 'Only rows where', whereRows(w.where || [], cols, 'w') + `<button type="button" class="bx-lnk" data-wadd>+ ${(w.where || []).length ? 'and' : 'add a condition'}</button>`)
        + presetQ(N(), w, cols.filter((c) => c.key !== w.lane))
        + title(N());
    } else if (t === 'grid' && kind === 'data') h += gridQ(B, W, w, N);
    else if (t === 'grid') h += '<p class="bx-hint">Companies down the side, document types across, one dot each — worst company first. Nothing else to set.</p>';
    else if (t === 'stats') h += Q(N(), 'Which tiles', inp('stat_items', (w.items || []).map((x) => x.key).join(', '), 'placeholder="companies, expired, expiring"'), 'Empty: all of them.') + title(N());
    return h;
  }
}

// a grid from three tables of the tool (1.0.63): rows (companies), columns (document types), and the cells —
// the rows of the source table that link to both, coloured by their status column
function gridQ(B, W, w, N) {
  const T = (name) => ((B.F.data[name] || {}).columns || []);
  const tables = [['', '— a table —'], ...Object.keys(B.F.data).map((x) => [x, W.text(B.F.data[x].title) || humanise(x)])];
  const colSel = (key, list, cur, empty = '— a column —') => sel(key, [['', empty], ...list.map((c) => [c.key, W.label(c)])], cur || '');
  const R = w.rows || {}, C = w.cols || {}, X = w.cell || {};
  const cells = T((w.source || {}).data);
  const links = (tbl) => cells.filter((c) => c.type === 'link' && c.table === tbl);
  const status = cells.filter((c) => c.type === 'calc' && (c.calc || {}).fn === 'expiry');
  const xs = new Set((w.extra || []).map((x) => (typeof x === 'string' ? x : x.key)));
  const pages = Object.keys(B.F.pages || {});
  return Q(N(), 'Down the side', `<div class="bx-row">the rows of ${sel('g:rows.data', tables, R.data)}</div>` + (R.data ? `<div class="bx-row bx-sub2">named by ${colSel('g:rows.label', T(R.data).filter((c) => ['text', 'choice'].includes(c.type || 'text')), R.label)} left off when ${colSel('g:rows.hide', T(R.data).filter((c) => c.type === 'bool'), R.hide, '— never —')}</div>` : ''))
    + Q(N(), 'Across the top', `<div class="bx-row">the rows of ${sel('g:cols.data', tables, C.data)}</div>` + (C.data ? `<div class="bx-row bx-sub2">headed by ${colSel('g:cols.label', T(C.data).filter((c) => ['text', 'choice'].includes(c.type || 'text')), C.label)} in the order of ${colSel('g:cols.order', T(C.data).filter((c) => ['int', 'number', 'text'].includes(c.type || 'text')), C.order, '— their names —')}</div><div class="bx-row bx-sub2">shown when ${colSel('g:cols.show', T(C.data).filter((c) => c.type === 'bool'), C.show, '— always —')}</div>` : ''))
    + Q(N(), 'Each dot', R.data && C.data ? `<div class="bx-row">is the row that links to ${colSel('g:cell.row', links(R.data), X.row)} and ${colSel('g:cell.col', links(C.data), X.col)}</div><div class="bx-row bx-sub2">coloured by ${colSel('g:cell.status', status, X.status)} dated by ${colSel('g:cell.date', cells.filter((c) => c.type === 'date'), X.date, '— no date —')}</div>` + (status.length ? '' : '<div class="bx-hint">The table needs a worked-out “status of a dated document” column first.</div>') : '<span class="bx-hint">Pick the two tables first.</span>', 'A dot nobody filed yet is empty: clicking it starts a new row with both links filled in.')
    + (R.data ? Q(N(), 'Next to the name', `<div class="bx-chips">${T(R.data).filter((c) => !['file', 'checklist', 'calc'].includes(c.type) && c.key !== R.label).map((c) => `<button type="button" data-gx="${esc(c.key)}" class="${xs.has(c.key) ? 'on' : ''}">${esc(W.label(c))}</button>`).join('')}</div>`, 'e.g. the responsible person. Clicking a name there shows only theirs.') : '')
    + Q(N(), 'Clicking a name opens', sel('g:row_page', [['', '— nothing —'], ...pages.map((p) => [p, W.text((B.F.pages[p] || {}).title) || humanise(p)])], w.row_page), 'That page gets the row in its address, so its tables show only that one.');
}

function actionsQ(n, w, rules, progress, W) {
  return Q(n, progress ? 'Buttons on each goal' : 'Buttons on each row', (w.actions || []).map((a, i) => a.kind === 'mail' ? `<div class="bx-grp"><div class="bx-row">${inp(`al:${i}`, W.text(a.label), 'placeholder="Mail" style="width:110px"')} <span class="bx-hint">mails someone about the row (to: ${esc((Array.isArray(a.to) ? a.to : [a.to]).filter(Boolean).join(' or ') || 'whoever they pick')})</span><button type="button" class="bx-x" data-ax="${i}">×</button></div></div>` : `<div class="bx-grp"><div class="bx-row">${inp(`al:${i}`, W.text(a.label), 'placeholder="e.g. Take it" style="width:110px"')} starts ${sel(`af:${i}`, [['', '— a rule —'], ...rules.map(([k, f]) => [k, W.text(f.title) || humanise(k)])], a.flow)}<button type="button" class="bx-x" data-ax="${i}">×</button></div>
    <div class="bx-row">Ask first ${sel(`aq:${i}`, [['', 'nothing'], ['date', 'for a date'], ['month', 'for a month'], ['text', 'for a line of words']], ((a.ask || [])[0] || {}).type || '')}${(a.ask || []).length ? inp(`aql:${i}`, W.text(a.ask[0].label), 'placeholder="The question, e.g. Which day?" style="flex:1"') : ''}</div>
    ${progress ? `<label class="bx-check"><input type="checkbox" data-areach="${i}" ${a.reached ? 'checked' : ''}> only once the target is reached</label>` : ''}</div>`).join('') + `<button type="button" class="bx-lnk" data-aadd>+ a button</button>${progress ? '' : ' <button type="button" class="bx-lnk" data-amail>+ a Mail button</button>'}`, 'The rule gets the row it was clicked on; what was asked is {answer} in the rule. A Mail button sends a message about the row, with a link to it, to someone CBX knows.');
}

// what a row added from this block starts with — e.g. the Team board's new cards are Kind: Team
function presetQ(n, w, cols) {
  const ch = cols.filter((c) => c.type === 'choice' && (c.options || []).length);
  const addQ = w.type === 'form' ? '' : `<div class="bx-row">An Add button ${seg('addbtn', [['1', 'Yes'], ['0', 'No']], w.add === false ? '0' : '1')}</div>`;
  if (!ch.length) return addQ ? Q(n, 'New rows', addQ) : '';
  const pre = w.preset || {};
  return Q(n, 'New rows added here start with', addQ + ch.map((c) => `<div class="bx-row">${esc(c.label)} ${sel(`pre:${c.key}`, [['', '— as the table says —'], ...c.options.map((o) => [o, o])], pre[c.key] || '')}</div>`).join(''), 'Filled in on the form; people can still change it.');
}

function wireBlock(host, B, W, w, at) {
  const redraw = () => B.mark();
  sels(host, (k, v) => {
    const m = k.match(/^pre:(.+)$/); if (!m) return;
    w.preset = { ...(w.preset || {}) }; if (v) w.preset[m[1]] = v; else delete w.preset[m[1]];
    if (!Object.keys(w.preset).length) delete w.preset;
    B.mark();
  });
  inps(host, (k, v) => {
    if (['title', 'text', 'label', 'confirm', 'submit', 'alt', 'caption'].includes(k)) { if (v || k === 'text') W.set(w, k, `w.${w.id}.${k}`, v); else delete w[k]; B.mark(false); return; }
    if (k === 'url' || k === 'code') { w[k] = v; B.mark(false); return; }
    if (k === 'api_path') { w.source.path = v; B.mark(false); return; }
    if (k === 'api_rows') { w.source.rows = v || '.'; B.mark(false); return; }
    if (k === 'stat_items') { const list = v.split(',').map((x) => x.trim()).filter(Boolean); if (list.length) w.items = list.map((key) => ({ key })); else delete w.items; B.mark(false); return; }
    const m = k.match(/^(ll|lu):(\d+)$/);
    if (m) { const it = w.items[+m[2]]; if (m[1] === 'll') W.set(it, 'label', `w.${w.id}.l${m[2]}`, v); else it.url = v; B.mark(false); return; }
    const a = k.match(/^al:(\d+)$/);
    if (a) { W.set(w.actions[+a[1]], 'label', `w.${w.id}.a${a[1]}`, v); B.mark(false); return; }
    const aq = k.match(/^aql:(\d+)$/);
    if (aq) { const q = w.actions[+aq[1]].ask[0]; W.set(q, 'label', `w.${w.id}.a${aq[1]}.ask`, v); B.mark(false); }
  });
  inps(host, (k, v) => { if (k === 'api_cols') { w.source.columns = v.split(',').map((x) => x.trim()).filter(Boolean).map((key) => ({ key: keyName(key), from: key, label: `col.${keyName(key)}`, type: 'text' })); for (const c of w.source.columns) if (!W.en[c.label]) W.en[c.label] = humanise(c.from); redraw(); } }, 'change');
  segs(host, (k, v) => {
    if (k === 'srckind') { const it = sourceItems(B, v, w.type)[0]; w.source = it ? JSON.parse(JSON.stringify(it.src)) : {}; delete w.columns; delete w.fields; w.where = []; delete w.sort; delete w.filters; if (w.type === 'chart') w.group = ''; }
    else if (k === 'level') w.level = +v; else if (k === 'tone') { if (v) w.tone = v; else delete w.tone; }
    else if (k === 'show') { if (v === 'totals') w.show = 'totals'; else delete w.show; }
    else if (k === 'sortdir') w.sort.desc = v === '1';
    else if (k === 'addbtn') { if (v === '0') w.add = false; else delete w.add; }
    else if (k === 'search') w.search = v === '1'; else if (k === 'row_link') { if (v === '1') w.row_link = true; else delete w.row_link; }
    else if (k === 'vfn') w.value = v === 'sum' ? { fn: 'sum', of: '' } : { fn: 'count' };
    else if (k === 'kind') w.kind = v; else if (k === 'primary') { if (v === '0') w.primary = false; else delete w.primary; }
    else if (k === 'height') w.height = +v; else if (k === 'align') w.align = v;
    redraw();
  });
  sels(host, (k, v) => {
    if (k === 'sortkey') { if (v) w.sort = { key: v, desc: !!(w.sort && w.sort.desc) }; else delete w.sort; }
    else if (k === 'group') w.group = v; else if (k === 'vof') w.value = { fn: 'sum', of: v };
    else if (k === 'plabel') { if (v) w.label = v; else delete w.label; }
    else if (k === 'ptarget') w.target = v;
    else if (k === 'pdata') w.count = { data: v, where: [] };
    else if (/^pc[fov]:\d+$/.test(k)) { const c = w.count.where[+k.split(':')[1]]; const f = { pcf: 'field', pco: 'op', pcv: 'value' }[k.split(':')[0]]; c[f] = v; if (f === 'op' && NO_VALUE.has(v)) delete c.value; }
    else if (k === 'lane') w.lane = v; else if (k === 'card') { if (v) w.card = v; else delete w.card; }
    else if (/^af:\d+$/.test(k)) w.actions[+k.slice(3)].flow = v;
    else if (/^aq:\d+$/.test(k)) { const act = w.actions[+k.slice(3)]; if (v) { const rest = (act.ask || []).slice(1); act.ask = [{ key: 'answer', type: v, label: (act.ask && act.ask[0] && act.ask[0].label) || `w.${w.id}.a${k.slice(3)}.ask` }, ...rest]; if (!W.en[act.ask[0].label]) W.en[act.ask[0].label] = v === 'date' ? 'Which day?' : v === 'month' ? 'Which month?' : 'What?'; } else delete act.ask; }
    else if (k === 'flow') w.flow = v; else if (k === 'asset') w.asset = v; else if (k === 'api_method') w.source.method = v;
    else { const m = k.match(/^lg:(\d+)$/); if (m) { const it = w.items[+m[1]]; delete it.url; delete it.route; delete it.params; if (v.startsWith('page:')) { it.route = 'tool'; it.params = { t: B.slug, p: v.slice(5) }; } else if (v === 'url') it.url = 'https://'; } else return; }
    redraw();
  });
  on(host, '[data-src]', 'click', (b) => { const kind = srcKind(w.source) || 'data'; const it = sourceItems(B, kind, w.type).find((i) => i.id === b.dataset.src); if (!it) return; w.source = JSON.parse(JSON.stringify(it.src)); delete w.columns; delete w.fields; w.where = []; delete w.sort; delete w.filters; if (w.type === 'chart') w.group = ''; redraw(); });
  const cols = srcColumns(B, w.source);
  const colEntry = (c) => { const key = `col.${c.key}`; if (!W.en[key]) W.en[key] = c.label; return { key: c.key, label: key, ...(c.type && c.type !== 'text' ? { type: c.type } : {}) }; };
  on(host, '[data-col]', 'click', (b) => { const k = b.dataset.col; const cur = (w.columns && w.columns.length ? w.columns : cols.map(colEntry)); w.columns = cur.some((c) => c.key === k) ? cur.filter((c) => c.key !== k) : [...cur, colEntry(cols.find((c) => c.key === k))]; redraw(); });
  on(host, '[data-field]', 'click', (b) => { const k = b.dataset.field; const cur = (w.fields && w.fields.length ? w.fields : cols.map((c) => ({ key: c.key }))); w.fields = cur.some((c) => c.key === k) ? cur.filter((c) => c.key !== k) : [...cur, { key: k }]; redraw(); });
  on(host, '[data-total]', 'click', (b) => { const k = b.dataset.total; const cur = new Set(w.totals || []); cur.has(k) ? cur.delete(k) : cur.add(k); w.totals = [...cur]; redraw(); });
  on(host, '[data-filter]', 'click', (b) => { const k = b.dataset.filter; const cur = new Set(w.filters || []); cur.has(k) ? cur.delete(k) : cur.add(k); w.filters = [...cur]; if (!w.filters.length) delete w.filters; redraw(); });
  on(host, '[data-bf]', 'click', (b) => { const k = b.dataset.bf; const cur = new Set((w.fields || []).map((f) => (typeof f === 'string' ? f : f.key))); cur.has(k) ? cur.delete(k) : cur.add(k); w.fields = [...cur].map((key) => ({ key })); if (!w.fields.length) delete w.fields; redraw(); });
  on(host, '[data-aadd]', 'click', () => { const i = (w.actions || []).length; (w.actions = w.actions || []).push({ label: '', flow: '' }); W.set(w.actions[i], 'label', `w.${w.id}.a${i}`, 'Do it'); redraw(); });
  on(host, '[data-ax]', 'click', (b) => { w.actions.splice(+b.dataset.ax, 1); if (!w.actions.length) delete w.actions; redraw(); });
  on(host, '[data-amail]', 'click', () => { const i = (w.actions || []).length; const bc = ((B.F.data[(w.source || {}).data] || {}).columns || []); const to = bc.filter((c) => c.type === 'person' || c.mail).map((c) => c.key); (w.actions = w.actions || []).push({ label: '', kind: 'mail', to, fields: bc.filter((c) => !['file', 'checklist'].includes(c.type)).slice(0, 5).map((c) => c.key) }); W.set(w.actions[i], 'label', `w.${w.id}.a${i}`, 'Mail'); redraw(); });
  // a grid of the tool's own tables (1.0.63): g:<part>.<setting>
  sels(host, (k, v) => { const m = k.match(/^g:(rows|cols|cell)\.(\w+)$/); if (m) { w[m[1]] = { ...(w[m[1]] || {}) }; if (v) w[m[1]][m[2]] = v; else delete w[m[1]][m[2]]; if (m[2] === 'data') { const keep = { data: v }; w[m[1]] = keep; } redraw(); } else if (k === 'g:row_page') { if (v) w.row_page = v; else delete w.row_page; redraw(); } });
  on(host, '[data-gx]', 'click', (b) => { const k = b.dataset.gx; const cur = (w.extra || []).map((x) => (typeof x === 'string' ? { key: x } : x)); w.extra = cur.some((x) => x.key === k) ? cur.filter((x) => x.key !== k) : [...cur, { key: k }]; if (!w.extra.length) delete w.extra; redraw(); });
  on(host, '[data-pcadd]', 'click', () => { (w.count.where = w.count.where || []).push({ field: '', op: 'equals', value: '' }); redraw(); });
  on(host, '[data-pcx]', 'click', (b) => { w.count.where.splice(+b.dataset.pcx, 1); redraw(); });
  on(host, '[data-pskip]', 'change', (c) => { const x = w.count.where[+c.dataset.pskip]; if (c.checked) x.skip_empty = true; else delete x.skip_empty; redraw(); });
  on(host, '[data-areach]', 'change', (c) => { const act = w.actions[+c.dataset.areach]; if (c.checked) act.reached = true; else delete act.reached; redraw(); });
  on(host, '[data-metric]', 'click', (b) => { const k = b.dataset.metric; w.items = (w.items || []).filter((x) => x.metric !== k); if (!b.classList.contains('on')) w.items.push({ metric: k }); redraw(); });
  on(host, '[data-wadd]', 'click', () => { (w.where = w.where || []).push({ field: (cols[0] || {}).key || '', op: 'equals', value: '' }); redraw(); });
  if (w.where) wireWhere(host, w.where, 'w', redraw);
  on(host, '[data-ladd]', 'click', () => { (w.items = w.items || []).push({ label: '', url: 'https://' }); redraw(); });
  on(host, '[data-lx]', 'click', (b) => { w.items.splice(+b.dataset.lx, 1); redraw(); });
  on(host, '[data-upload]', 'click', () => B.upload((name) => { w.asset = name; redraw(); }));
  on(host, '[data-newrule]', 'click', () => { const name = B.newRule('button'); w.flow = name; B.select({ kind: 'rule', id: name }); });
  on(host, '[data-mv]', 'click', (b) => { const d = +b.dataset.mv, list = at.col.widgets, j = at.i + d; if (j < 0 || j >= list.length) return; [list[at.i], list[j]] = [list[j], list[at.i]]; redraw(); });
  on(host, '[data-side]', 'click', () => { const cols2 = at.sec.columns; const to = cols2[(at.ci + 1) % cols2.length]; at.col.widgets.splice(at.i, 1); to.widgets.push(w); redraw(); });
  on(host, '[data-del]', 'click', () => { at.col.widgets.splice(at.i, 1); B.select({ kind: 'page' }); });
}

// ---- a table of the tool's own -------------------------------------------------------------------------
const CTYPES = [['text', 'Words'], ['number', 'A number'], ['int', 'A whole number'], ['date', 'A date'], ['bool', 'Yes / no'], ['choice', 'A pick-list'], ['link', 'A row of another table'], ['person', 'A person'], ['checklist', 'A checklist'], ['file', 'A file'], ['calc', 'Worked out']];
const CALCS = [['count', 'How many checklist items'], ['done', 'How many are ticked'], ['days', 'Days between two dates'], ['grade', 'A word for a number (e.g. Small / Medium / Large)'], ['expiry', 'Where a dated document stands (in date, expiring, expired …)']];
function calcEditor(c, i, cols, W) {
  const cc = c.calc || {};
  const pick = (types, extra = []) => [['', '— a column —'], ...extra, ...cols.filter((x) => x !== c && types.includes(x.type || 'text')).map((x) => [x.key, W.label(x)])];
  let h = `<div class="bx-row bx-sub2">How ${sel(`cf:${i}`, CALCS, cc.fn || '')}</div>`;
  if (cc.fn === 'count' || cc.fn === 'done') h += `<div class="bx-row bx-sub2">Of ${sel(`cof:${i}`, pick(['checklist']), cc.of)}</div>`;
  if (cc.fn === 'days') h += `<div class="bx-row bx-sub2">From ${sel(`cfr:${i}`, pick(['date'], [['created', 'when the row was added'], ['today', 'today']]), cc.from || 'created')} to ${sel(`cto:${i}`, pick(['date'], [['today', 'today'], ['created', 'when the row was added']]), cc.to || 'today')}</div>`;
  if (cc.fn === 'expiry') {
    h += `<div class="bx-row bx-sub2">Expires on ${sel(`cxd:${i}`, pick(['date']), cc.date)} warn ${sel(`cxw:${i}`, pick(['int', 'number'], [['', '90 days before']]), cc.warn || '')}</div>`
      + `<div class="bx-row bx-sub2">Required when ${sel(`cxr:${i}`, pick(['bool'], [['', 'always']]), cc.required || '')} doesn't expire when ${sel(`cxn:${i}`, pick(['bool'], [['', 'never']]), cc.never || '')}</div>`
      + `<div class="bx-row bx-sub2">Its file ${sel(`cxf:${i}`, pick(['file'], [['', '— none —']]), cc.file || '')} archived when ${sel(`cxo:${i}`, pick(['bool'], [['', 'never']]), cc.off || '')}</div>`;
  }
  if (cc.fn === 'grade') {
    h += `<div class="bx-row bx-sub2">Of ${sel(`cof:${i}`, pick(['int', 'number', 'calc']), cc.of)}</div>`;
    h += (cc.grades || []).map((g, j, all) => `<div class="bx-row bx-sub2">${j === all.length - 1 ? 'anything more' : `up to ${inp(`cgt:${i}:${j}`, g[0] ?? '', 'style="width:60px"')}`} → ${inp(`cgw:${i}:${j}`, g[1], 'style="width:110px"')}</div>`).join('') + `<button type="button" class="bx-lnk bx-sub2" data-gadd2="${i}">+ a step</button>`;
  }
  return h;
}

function membersQ(B, W, d, name) {
  const mm = d.members || {};
  const mt = B.F.data[mm.table] || { columns: [] };
  const mcols = mt.columns || [];
  const own = (d.columns || []).filter((c) => c.type === 'link');
  const statusCols = mcols.filter((c) => c.type === 'choice');
  return `<div class="bx-row bx-sub2">Members are the rows of ${sel('mtable', [['', '— a table —'], ...Object.keys(B.F.data).filter((x) => x !== name).map((x) => [x, W.text(B.F.data[x].title) || humanise(x)])], mm.table)}</div>`
    + (mm.table ? `<div class="bx-row bx-sub2">the person is ${sel('mperson', [['', '—'], ...mcols.filter((c) => c.type === 'person').map((c) => [c.key, W.label(c)])], mm.person)} and they belong to ${sel('mlink', [['', '—'], ...mcols.filter((c) => c.type === 'link').map((c) => [c.key, W.label(c)])], mm.link)}</div>
      <div class="bx-row bx-sub2">which is ${sel('mthrough', [['id', 'this row'], ...own.map((c) => [c.key, 'the row in ' + W.label(c)])], mm.through || 'id')}</div>
      ${statusCols.length ? `<div class="bx-row bx-sub2">only when ${sel('monly', [['', 'always'], ...statusCols.flatMap((c) => (c.options || []).map((o) => [`${c.key}=${o}`, `${W.label(c)} is ${o}`]))], Object.entries(mm.only || {}).map(([k, v]) => `${k}=${v}`)[0] || '')}</div>` : ''}` : '');
}

function linkedQ(W, d) {
  const links = (d.columns || []).filter((c) => c.type === 'link');
  return `<div class="bx-row bx-sub2">the row in ${sel('slink', [['', '— a link column —'], ...links.map((c) => [c.key, W.label(c)])], d.link || '')}</div>`;
}

// 1.0.63: everyone with the tool sees the rows where a pick-list or yes/no column has a value
function openQ(W, d) {
  const op = d.open || {};
  const opts = (d.columns || []).flatMap((c) => (c.type === 'choice' ? (c.options || []).map((o) => [`${c.key}=${o}`, `${W.label(c)} is ${o}`]) : c.type === 'bool' ? [[`${c.key}=true`, `${W.label(c)} is yes`]] : []));
  if (!opts.length) return '';
  return `<div class="bx-row bx-sub2">and everyone with the tool sees the rows where ${sel('open', [['', '— never —'], ...opts], op.field ? `${op.field}=${op.value === true ? 'true' : op.value}` : '')}</div><div class="bx-hint bx-sub2">Someone given the tool “only for what is theirs” (People &amp; access) still sees only theirs.</div>`;
}

function shareQ(W, d) {
  const sh = d.share || {};
  const opts = (d.columns || []).filter((c) => c.type === 'choice').flatMap((c) => (c.options || []).map((o) => [`${c.key}=${o}`, `${W.label(c)} is ${o}`]));
  if (!opts.length) return '';
  return `<div class="bx-row bx-sub2">and the whole team sees the rows where ${sel('share', [['', '— never —'], ...opts], sh.field ? `${sh.field}=${sh.value}` : '')}</div>`;
}

function exposeQ(W, d) {
  const on = new Set((d.expose || {}).columns || []);
  const cols = (d.columns || []).filter((c) => c.type !== 'file' && c.key);
  const warn = on.size && (d.scope || 'all') !== 'all' ? '<div class="small" style="color:var(--warn);margin-top:6px">This table is not “Everyone sees all” here — a key still reads all its rows.</div>' : '';
  return `<div class="bx-row" style="flex-wrap:wrap;gap:6px">${cols.map((c) => `<label class="bx-check"><input type="checkbox" data-ex="${esc(c.key)}" ${on.has(c.key) ? 'checked' : ''}> ${esc(W.label(c))}</label>`).join('')}</div>`
    + `<div class="small muted">${on.size ? `${on.size} column${on.size === 1 ? '' : 's'} may be read` : 'Nothing is read from outside — tick a column to allow it.'}</div>` + warn;
}

function askData(host, B, name) {
  const W = words(B), d = B.F.data[name];
  if (!d) { B.select({ kind: 'page' }); return; }
  const cols = d.columns || [];
  const others = Object.keys(B.F.data).filter((x) => x !== name);
  host.innerHTML = head('Table of this tool', W.text(d.title) || humanise(name))
    + Q(1, 'What the table holds', inp('dtitle', W.text(d.title) || humanise(name)), `Kept as <span class="mono">${esc(name)}</span> — its rows are in this tool’s own database.`)
    + Q(2, 'Its columns', cols.map((c, i) => `<div class="bx-colrow"><div class="bx-row">${inp(`cl:${i}`, W.label(c), 'style="flex:1"')}${sel(`ct:${i}`, CTYPES, c.type || 'text')}<button type="button" class="bx-x" data-cx="${i}" aria-label="Remove">×</button></div>
        ${c.type === 'choice' ? `<div class="bx-row bx-sub2">Options ${inp(`co:${i}`, (c.options || []).join(', '), 'placeholder="Open, In progress, Done" style="flex:1"')}</div>` : ''}
        ${c.type === 'link' ? `<div class="bx-row bx-sub2">Points at ${sel(`cg:${i}`, [['', '— a table —'], ...Object.keys(B.F.data).map((x) => [x, W.text(B.F.data[x].title) || humanise(x)])], c.table)}</div>` : ''}
        ${c.type === 'choice' && (c.options || []).length ? `<div class="bx-row bx-sub2">Starts as ${sel(`cd:${i}`, [['', '— empty —'], ...c.options.map((o) => [o, o])], c.default || '')}</div>` : ''}
        ${c.type === 'date' ? `<div class="bx-row bx-sub2">Starts as ${sel(`cd:${i}`, [['', '— empty —'], ['today', 'today']], c.default || '')}</div>` : ''}
        ${['number', 'int'].includes(c.type) ? `<div class="bx-row bx-sub2">Shown as ${sel(`cfm:${i}`, [['', 'a plain number'], ['money', 'money (R 1 250.00)']], c.format || '')}</div>` : ''}
        ${c.type === 'person' ? `<div class="bx-row bx-sub2">Who can be picked ${sel(`cw:${i}`, [['everyone', 'Everyone'], ['team', 'Their own team']], c.who || 'everyone')} starts as ${sel(`cd:${i}`, [['', '— nobody —'], ['me', 'the person who adds it']], c.default || '')}</div>` : ''}
        ${c.type === 'calc' ? calcEditor(c, i, cols, W) : ''}
        ${c.type === 'file' ? `<div class="bx-row bx-sub2">Takes ${inp(`ca:${i}`, (c.accept || []).join(', '), 'placeholder="pdf, png, xlsx (empty: documents and pictures)" style="flex:1"')} up to ${sel(`cm:${i}`, [['5', '5 MB'], ['10', '10 MB'], ['25', '25 MB'], ['100', '100 MB']], String(c.max_mb || 10))}</div><div class="bx-row bx-sub2">A new file ${sel(`ck:${i}`, [['0', 'replaces the old one'], ['3', 'keeps the last 3 earlier ones'], ['10', 'keeps the last 10 earlier ones'], ['25', 'keeps the last 25 earlier ones']], String(c.keep || 0))}</div>` : ''}
        ${(c.type || 'text') === 'text' ? `<label class="bx-check bx-sub2"><input type="checkbox" data-cmail="${i}" ${c.mail ? 'checked' : ''}> holds an e-mail address CBX may write to</label>` : ''}
        <label class="bx-check"><input type="checkbox" data-cr="${i}" ${c.required ? 'checked' : ''}> must be filled in</label></div>`).join('') + `<button type="button" class="bx-lnk" data-cadd>+ a column</button>`, 'Every table also keeps who added each row and when. A column you remove stays in the database, unused — nothing is thrown away.')
    + Q(3, 'Who may add rows', seg('write', [['everyone', 'Everyone with the tool'], ['managers', 'Managers and admins'], ['admins', 'Admins only'], ['nobody', 'Nobody (read only)']], d.write || 'everyone'))
    + Q(4, 'Who sees the rows', seg('scope', [['all', 'Everyone sees all'], ['mine', 'Each person their own'], ['team', 'Each team its own'], ['members', 'The members of …'], ['linked', 'Whoever sees the row it links to']], d.scope || 'all') + (d.scope === 'members' ? membersQ(B, W, d, name) : '') + (d.scope === 'mine' ? shareQ(W, d) : '') + (d.scope === 'linked' ? linkedQ(W, d) : '') + (['mine', 'team', 'members'].includes(d.scope) ? openQ(W, d) : ''), (d.scope || 'all') === 'mine' ? 'Their own: rows they added or are named in. A manager also sees their team’s. Admins see all.' : (d.scope || 'all') === 'team' ? 'The rows added by anyone in the person’s team, and rows they are named in. Admins see all.' : d.scope === 'linked' ? 'e.g. a company’s documents: whoever may see the company sees its documents. Admins see all.' : '')
    + Q('', 'Who may change a row', seg('edit', [['own', 'Their own rows (admins any)'], ['seen', 'Anyone who may see it']], d.edit || 'own'), 'Rows they added or are named in — or every row they can see (e.g. anyone may set a document’s date or file its renewal).')
    + Q(5, 'Only once', (d.unique || []).map((g, gi) => `<div class="bx-row">${g.map((k) => `<span class="pill">${esc(W.label(cols.find((c) => c.key === k) || { key: k }))}</span>`).join(' + ')}<button type="button" class="bx-x" data-ux="${gi}">×</button></div>`).join('') + `<div class="bx-row">${sel('uadd', [['', '+ these columns together only once …'], ...cols.map((c) => [c.key, W.label(c)])], '')}</div>`, 'e.g. Goal + Person: one reward day per goal and person. Pick the columns one after the other.')
    + Q(6, 'Other systems may read it', exposeQ(W, d), 'Only with a key the owner makes (API connections → Keys for our data), only the columns ticked, reading only — never files. A key is not a person: it reads every row, whoever sees them here.')
    + `<div class="bx-foot"><span style="flex:1"></span><button type="button" class="bx-lnk danger" data-deldata>Remove this table</button></div>`;
  void others;
  inps(host, (k, v) => {
    if (k === 'dtitle') { W.set(d, 'title', `data.${name}.title`, v); B.mark(false); return; }
    let m = k.match(/^cl:(\d+)$/); if (m) {
      const c = cols[+m[1]];
      // a column not saved yet takes its name from its words; a saved one keeps its name (its data is under it)
      if (c._new) { let k2 = keyName(v), i = 2; while (cols.some((x) => x !== c && x.key === k2)) k2 = `${keyName(v)}_${i++}`; delete W.en[c.label]; c.key = k2; c.label = `col.${k2}`; }
      W.set(c, 'label', `col.${c.key}`, v); B.mark(false); return;
    }
  });
  inps(host, (k, v) => {
    if (k === 'dtitle' && d._new) { const old = d.title; const n2 = B.rename('data', name, v); if (n2 !== name) { delete W.en[old]; d.title = ''; W.set(d, 'title', `data.${n2}.title`, v); B.sel = { kind: 'data', id: n2 }; } B.mark(); return; }
    let m = k.match(/^co:(\d+)$/); if (m) { const c = cols[+m[1]]; c.options = v.split(',').map((x) => x.trim()).filter(Boolean); for (const o of c.options) W.en[`opt.${name}.${c.key}.${optSlug(o)}`] = o; B.mark(); return; }
    m = k.match(/^ca:(\d+)$/); if (m) { const c = cols[+m[1]]; const a = v.split(',').map((x) => x.trim().toLowerCase().replace(/^\./, '')).filter(Boolean); if (a.length) c.accept = a; else delete c.accept; B.mark(); }
  }, 'change');
  sels(host, (k, v) => {
    let m = k.match(/^ct:(\d+)$/); if (m) { const c = cols[+m[1]]; c.type = v; delete c.options; delete c.table; delete c.show; delete c.accept; delete c.max_mb; delete c.format; delete c.default; if (v === 'choice') c.options = []; if (v === 'link') c.table = ''; if (v === 'file') c.max_mb = 10; B.mark(); return; }
    m = k.match(/^cg:(\d+)$/); if (m) { cols[+m[1]].table = v; B.mark(); return; }
    m = k.match(/^cm:(\d+)$/); if (m) { cols[+m[1]].max_mb = +v; B.mark(); return; }
    m = k.match(/^cd:(\d+)$/); if (m) { if (v) cols[+m[1]].default = v; else delete cols[+m[1]].default; B.mark(); return; }
    m = k.match(/^cfm:(\d+)$/); if (m) { if (v) cols[+m[1]].format = v; else delete cols[+m[1]].format; B.mark(); return; }
    m = k.match(/^cw:(\d+)$/); if (m) { if (v === 'team') cols[+m[1]].who = 'team'; else delete cols[+m[1]].who; B.mark(); return; }
    m = k.match(/^cf:(\d+)$/); if (m) { cols[+m[1]].calc = v === 'grade' ? { fn: 'grade', of: '', grades: [[3, 'Small'], [7, 'Medium'], [null, 'Large']] } : v === 'days' ? { fn: 'days', from: 'created', to: 'today' } : v === 'expiry' ? { fn: 'expiry', date: (cols.find((c) => c.type === 'date') || {}).key || '' } : { fn: v, of: '' }; B.mark(); return; }
    m = k.match(/^cx([dwrnfo]):(\d+)$/); if (m) { const cc = cols[+m[2]].calc; const f = { d: 'date', w: 'warn', r: 'required', n: 'never', f: 'file', o: 'off' }[m[1]]; if (v) cc[f] = v; else delete cc[f]; B.mark(); return; }
    m = k.match(/^ck:(\d+)$/); if (m) { if (+v) cols[+m[1]].keep = +v; else delete cols[+m[1]].keep; B.mark(); return; }
    m = k.match(/^c(of|fr|to):(\d+)$/); if (m) { const cc = cols[+m[2]].calc; cc[{ of: 'of', fr: 'from', to: 'to' }[m[1]]] = v; B.mark(); }
  });
  inps(host, (k, v) => {
    const m = k.match(/^cg(t|w):(\d+):(\d+)$/); if (!m) return;
    const g = cols[+m[2]].calc.grades[+m[3]];
    if (m[1] === 't') g[0] = v === '' ? null : +v; else g[1] = v;
    B.mark(false);
  }, 'change');
  on(host, '[data-gadd2]', 'click', (b) => { const g = cols[+b.dataset.gadd2].calc.grades; g.splice(g.length - 1, 0, [0, 'New']); B.mark(); });
  on(host, '[data-cr]', 'change', (c) => { const col = cols[+c.dataset.cr]; if (c.checked) col.required = true; else delete col.required; B.mark(false); });
  on(host, '[data-cmail]', 'change', (c) => { const col = cols[+c.dataset.cmail]; if (c.checked) col.mail = true; else delete col.mail; B.mark(false); });
  on(host, '[data-cadd]', 'click', () => { let k = 'column', i = 2; while (cols.some((c) => c.key === k)) k = `column_${i++}`; cols.push({ key: k, type: 'text', label: `col.${k}`, _new: true }); W.en[`col.${k}`] = 'New column'; d.columns = cols; B.mark(); });
  on(host, '[data-cx]', 'click', (b) => { cols.splice(+b.dataset.cx, 1); B.mark(); });
  segs(host, (k, v) => { if (k === 'edit') { if (v === 'seen') d.edit = 'seen'; else delete d.edit; B.mark(); return; } d[k] = v; if (k === 'scope' && v === 'members' && !d.members) d.members = { table: '', link: '', person: '', through: 'id', only: {} }; if (k === 'scope' && v !== 'members') delete d.members; if (k === 'scope' && v !== 'mine') delete d.share; if (k === 'scope' && v !== 'linked') delete d.link; if (k === 'scope' && !['mine', 'team', 'members'].includes(v)) delete d.open; B.mark(); });
  sels(host, (k, v) => {
    if (k === 'uadd' && v) { B._uniq = [...(B._uniq || []), v]; if (B._uniq.length >= 2) { d.unique = [...(d.unique || []), B._uniq]; B._uniq = []; } else toast('Now pick the column it goes together with.'); B.mark(); return; }
    if (k === 'share') { const [col, val] = v.split('='); if (col) d.share = { field: col, value: val }; else delete d.share; B.mark(); return; }
    if (k === 'slink') { if (v) d.link = v; else delete d.link; B.mark(); return; }
    if (k === 'open') { const [col, val] = v.split('='); if (col) d.open = { field: col, value: val === 'true' ? true : val }; else delete d.open; B.mark(); return; }
    if (k === 'mtable') { d.members = { table: v, link: '', person: '', through: 'id', only: {} }; B.mark(); return; }
    if (k === 'mlink') { d.members.link = v; B.mark(); return; }
    if (k === 'mperson') { d.members.person = v; B.mark(); return; }
    if (k === 'mthrough') { d.members.through = v; B.mark(); return; }
    if (k === 'monly') { const [col, val] = v.split('='); d.members.only = col ? { [col]: val } : {}; B.mark(); }
  });
  on(host, '[data-ex]', 'change', (c) => {
    const keys = new Set((d.expose || {}).columns || []);
    if (c.checked) keys.add(c.dataset.ex); else keys.delete(c.dataset.ex);
    const ordered = cols.map((x) => x.key).filter((k) => keys.has(k));
    if (ordered.length) d.expose = { columns: ordered }; else delete d.expose;
    B.mark();
  });
  on(host, '[data-ux]', 'click', (b) => { d.unique.splice(+b.dataset.ux, 1); if (!d.unique.length) delete d.unique; B.mark(); });
  on(host, '[data-deldata]', 'click', async () => { if (!(await confirmBox(`Remove the table “${W.text(d.title) || name}” from the tool? Its rows stay in the database; parts that show it will say it is gone.`, 'Remove'))) return; delete B.F.data[name]; B.select({ kind: 'page' }); });
}

// ---- a rule -----------------------------------------------------------------------------------------------
const WHO = [['starter', 'The person who started it'], ['owner', 'The tool’s owner'], ['person', 'A person in the row'], ['manager_of', 'The team lead of a person in the row'], ['roles', 'Everyone with a role'], ['people', 'People by email']];
function askRule(host, B, name) {
  const W = words(B), f = B.F.flows[name];
  if (!f) { B.select({ kind: 'page' }); return; }
  B.rules = B.rules || {};
  const R = B.rules[name] = B.rules[name] || decompile(f);
  const titleBar = head('Rule', W.text(f.title) || humanise(name), `<div class="bx-seg" data-seg="on"><button type="button" data-v="1" class="${f.enabled !== false ? 'on' : ''}">On</button><button type="button" data-v="0" class="${f.enabled === false ? 'on' : ''}">Off</button></div>`);
  if (R.readOnly) {
    host.innerHTML = titleBar + `<div class="bx-note warn">${esc(R.readOnly)}</div>` + Q('', 'Its name', inp('rtitle', W.text(f.title))) + `<div class="bx-foot"><span style="flex:1"></span><button type="button" class="bx-lnk danger" data-delrule>Remove this rule</button></div>`;
    inps(host, (k, v) => { if (k === 'rtitle') { W.set(f, 'title', `flow.${name}.title`, v); B.mark(false); } });
    segs(host, (k, v) => { if (k === 'on') { f.enabled = v === '1'; B.mark(); } });
    on(host, '[data-delrule]', 'click', () => delRule());
    return;
  }
  const tables = Object.keys(B.F.data || {});
  const wh = R.when || (R.when = {});
  // the rows a rule works on: its "go through" source, or the row that started it
  const goCols = R.go ? srcColumns(B, R.go.sheet ? { sheet: R.go.sheet } : R.go.provider ? { provider: R.go.provider } : { data: R.go.data }) : [];
  const trigCols = ['row_added', 'row_changed'].includes(wh.type) || (wh.type === 'button' && wh.data) ? srcColumns(B, { data: wh.data }) : [];
  const rowName = R.go ? (R.go.each ? R.go.item : null) : trigCols.length || ['row_added', 'row_changed'].includes(wh.type) ? 'trigger.row' : null;
  const rowCols = R.go ? goCols : trigCols;
  const tok = (text) => showTokens(String(text ?? '').replace(/\{\{\s*trigger\.values\.answer\s*\}\}/g, '{answer}').replace(new RegExp(`\\{\\{\\s*${(R.go && R.go.as) || 'rows'}_count\\s*\\}\\}`, 'g'), '{count}'), rowName, rowCols);
  const chipsFor = () => `<div class="bx-tokens">Put in: ${[...rowCols.map((c) => c.label), ...(R.go ? ['count'] : []), ...(wh.type === 'button' && wh.data ? ['answer'] : []), 'today'].map((x) => `<button type="button" data-tok="${esc(x)}">{${esc(x)}}</button>`).join('')}</div>`;
  const srcOpts = [...tables.map((t) => [`data:${t}`, 'This tool · ' + (W.text(B.F.data[t].title) || humanise(t))]), ...(B.ov.sheets || []).map((s) => [`sheet:${s.slug}`, 'Warehouse · ' + s.name]), ...(B.ov.providers || []).filter((p) => p.kind === 'table').map((p) => [`provider:${p.name}`, 'Another tool · ' + p.title])];
  const goVal = R.go ? (R.go.sheet ? `sheet:${R.go.sheet}` : R.go.provider ? `provider:${R.go.provider}` : `data:${R.go.data}`) : '';

  let whenInner = seg('wtype', WHEN, wh.type || '');
  if (wh.type === 'row_added' || wh.type === 'row_changed') whenInner += `<div class="bx-row">in ${sel('wdata', [['', '— a table —'], ...tables.map((t) => [t, W.text(B.F.data[t].title) || humanise(t)])], wh.data)}</div>`;
  if (wh.type === 'schedule') {
    whenInner += `<div class="bx-row">${sel('wevery', EVERY, wh.every || 'weekday')}${wh.every === 'hour' ? '' : `at ${inp('wat', wh.at || '07:00', 'style="width:70px"')}`}${wh.every === 'week' ? sel('wday', DAYS, wh.day || 'mon') : ''}${wh.every === 'month' ? `on day ${sel('wmday', Array.from({ length: 28 }, (_, i) => [i + 1, String(i + 1)]), wh.day || 1)}` : ''}</div><div class="bx-hint">A schedule runs as the tool’s owner (South African time).</div>`;
  }
  if (wh.type === 'button') whenInner += `<div class="bx-row">on ${sel('wbrow', [['', 'the page (a Button)'], ...tables.map((t) => [t, 'a row of ' + (W.text(B.F.data[t].title) || humanise(t))])], wh.data || '')}</div><div class="bx-hint">${wh.data ? 'Put it on a table as a button on each row; the rule gets that row.' : 'Put a Button on a page and choose this rule for it.'}</div>`;
  const goInner = R.go ? `<div class="bx-row">${sel('gosrc', srcOpts, goVal)}</div>` + whereRows(R.go.where, goCols, 'g') + `<button type="button" class="bx-lnk" data-gadd>+ ${R.go.where.length ? 'and' : 'only rows where …'}</button>`
    + `<div class="bx-row">${seg('goeach', [['1', 'Do the steps below for each row'], ['0', 'Once, with the whole list']], R.go.each ? '1' : '0')}</div>` + `<button type="button" class="bx-lnk danger" data-gooff style="justify-self:start">Don’t go through rows</button>`
    : `<button type="button" class="bx-lnk" data-goon>+ go through the rows of a table</button>`;
  const onlyInner = R.only.map((g, gi) => `<div class="bx-grp"><div class="bx-row">${sel(`om:${gi}`, [['all', 'All of these'], ['any', 'Any of these']], g.match)}<button type="button" class="bx-x" data-ogx="${gi}">×</button></div>${g.conds.map((c, i) => {
    const m = String(c.left || '').match(/^\{\{\s*([^}]+)\s*\}\}$/); const fieldKey = m && rowName && m[1].startsWith(rowName + '.') ? m[1].slice(rowName.length + 1) : null;
    return `<div class="bx-row">${rowCols.length && (fieldKey !== null || !c.left) ? sel(`ol:${gi}:${i}`, [['', '— column —'], ...rowCols.map((x) => [x.key, x.label])], fieldKey) : inp(`olt:${gi}:${i}`, tok(c.left), 'style="width:120px"')}${sel(`oo:${gi}:${i}`, OPS, c.op || 'equals')}${NO_VALUE.has(c.op) ? '' : inp(`ov:${gi}:${i}`, tok(c.value), 'style="width:100px"')}<button type="button" class="bx-x" data-ox="${gi}:${i}">×</button></div>`; }).join('')}<button type="button" class="bx-lnk" data-oadd="${gi}">+ and</button></div>`).join('')
    + `<button type="button" class="bx-lnk" data-ogadd>+ ${R.only.length ? 'and also' : 'only if …'}</button>`;
  // which row a step works on: the row that started the rule, or a row an earlier step added
  const rowChoices = (target, list, i) => {
    const out = [];
    if (rowName && target && target === ((R.go && R.go.each && R.go.data) || wh.data)) out.push([`{{${rowName}.id}}`, 'the row that started it']);
    const seq = list === 'after' ? [...R.then, ...R.after.slice(0, i)] : list === 'then' ? R.then.slice(0, i) : [];
    seq.forEach((st, j) => { if (st.type === 'add_row' && st.data === target) out.push([`{{${st.as || 'row'}.id}}`, `the row added by step ${j + 1}`]); });
    return out;
  };
  const rowSel = (P, target, cur, list, i) => { const ch = rowChoices(target, list, i); return ch.length > 1 || (cur && !ch.some(([v]) => v === cur)) ? `<div class="bx-row">which row ${sel(`srow:${P}`, [...ch, ...(cur && !ch.some(([v]) => v === cur) ? [[cur, cur]] : [])], cur || (ch[0] || [''])[0])}</div>` : ''; };
  const stepHtml = (s, i, list) => {
    const P = `${list}:${i}`;
    let h = sel(`sk:${P}`, DO, s.type);
    const who = (w) => `<div class="bx-row">to ${sel(`swk:${P}`, WHO, (w || {}).kind || 'owner')}${(w || {}).kind === 'roles' ? `<span class="bx-chips">${['owner', 'admin', 'manager', 'user'].map((r) => `<button type="button" data-swr="${P}:${r}" class="${((w || {}).roles || []).includes(r) ? 'on' : ''}">${humanise(r)}</button>`).join('')}</span>` : ''}${(w || {}).kind === 'people' ? inp(`swe:${P}`, tok(w.emails), 'placeholder="name@company.com, {Email}" style="flex:1"') : ''}${['person', 'manager_of'].includes((w || {}).kind) ? sel(`swp:${P}`, [['{{person.id}}', 'the person it runs for'], ...rowCols.filter((c) => c.type === 'person').map((c) => [`{{${rowName}.${c.key}}}`, c.label])], (w || {}).person || '{{person.id}}') : ''}</div>`;
    if (s.type === 'notify') h += who(s.who) + inp(`st:${P}`, tok(s.title), 'placeholder="Title"') + area(`sx:${P}`, tok(s.text), 60);
    if (s.type === 'email') h += `<div class="bx-row">to ${inp(`sto:${P}`, tok(s.to), 'placeholder="{Email} or name@company.com" style="flex:1"')}</div>` + inp(`ssu:${P}`, tok(s.subject), 'placeholder="Subject"') + area(`sx:${P}`, tok(s.text), 80) + `<div class="bx-hint">Only to people CBX-Connect knows, or the company’s own addresses.</div>`;
    if (s.type === 'approval') {
      h += who(s.who) + area(`sq:${P}`, tok(s.question), 50) + `<div class="bx-hint">They get Approve / Decline under Approvals. The steps after this one run when it is approved.</div>`;
      if (!list.startsWith('d')) h += `<div class="bx-sub">If declined</div><div class="bx-stack bx-declined">${(s.declined || []).map((d, j) => stepHtml(d, j, `d${list}.${i}`)).join('')}<button type="button" class="bx-lnk" data-sadd="d${list}.${i}">+ ${(s.declined || []).length ? 'then' : 'do something when declined'}</button></div>`;
    }
    if (s.type === 'update_row' || s.type === 'add_row') {
      const target = s.type === 'update_row' && !s.data ? (R.go && R.go.data) || wh.data || '' : s.data;
      const tcols = srcColumns(B, { data: target });
      h += `<div class="bx-row">${s.type === 'update_row' ? 'of' : 'to'} ${sel(`sd:${P}`, [['', '— a table —'], ...tables.map((t) => [t, W.text(B.F.data[t].title) || humanise(t)])], target)}</div>` + (s.type === 'update_row' ? rowSel(P, target, s.row, list, i) : '') + tcols.map((c) => `<div class="bx-row"><span class="bx-hint" style="min-width:90px">${esc(c.label)}</span>${inp(`sv:${P}:${c.key}`, tok((s.values || {})[c.key]), `placeholder="${s.type === 'update_row' ? 'leave empty to keep' : ''}" style="flex:1"`)}</div>`).join('');
    }
    if (s.type === 'wait') h += `<div class="bx-row">${['days', 'hours', 'minutes'].map((k) => `${inp(`sw:${P}:${k}`, s[k] || '', 'style="width:52px"')} ${k}`).join(' ')}</div>`;
    if (s.type === 'api') h += `<div class="bx-row">${sel(`sac:${P}`, [['', '— a connection —'], ...(B.ov.connections || []).map((c) => [c.slug, c.name])], s.connection)}${sel(`sam:${P}`, ['GET', 'POST', 'PUT', 'PATCH'], s.method || 'GET')}</div>` + inp(`sap:${P}`, s.path, 'placeholder="/path"');
    if (s.type === 'set') h += `<div class="bx-row">${inp(`sn:${P}`, s.name, 'placeholder="a name" style="width:110px"')} = ${inp(`sva:${P}`, tok(s.value), 'style="flex:1"')}</div>`;
    if (s.type === 'require') {
      h += `<div class="bx-row">${sel(`srm:${P}`, [['all', 'All of these'], ['any', 'Any of these']], s.match || 'all')}</div>` + (s.when || []).map((c, j) => {
        const mm = String(c.left || '').match(/^\{\{\s*([^}]+)\s*\}\}$/); const fk = mm && rowName && mm[1].startsWith(rowName + '.') ? mm[1].slice(rowName.length + 1) : null;
        return `<div class="bx-row">${rowCols.length && (fk !== null || !c.left) ? sel(`srl:${P}:${j}`, [['', '— column —'], ...rowCols.map((x) => [x.key, x.label])], fk) : inp(`srlt:${P}:${j}`, tok(c.left), 'style="width:120px"')}${sel(`sro:${P}:${j}`, OPS, c.op || 'equals')}${NO_VALUE.has(c.op) ? '' : inp(`srv:${P}:${j}`, tok(c.value), 'style="width:100px"')}<button type="button" class="bx-x" data-srx="${P}:${j}">×</button></div>`;
      }).join('') + `<button type="button" class="bx-lnk" data-sradd="${P}">+ and</button>` + inp(`srmsg:${P}`, tok(s.message), 'placeholder="Otherwise, say: e.g. Tick every line to check first."');
    }
    if (s.type === 'action' || s.type === 'make_doc') {
      // both work on one row: the row that started the rule, unless another table is chosen
      const target = s.data || (R.go && R.go.data) || wh.data || '';
      const tcols = srcColumns(B, { data: target });
      const colSel = (key, want, cur) => sel(key, [['', want === 'file' ? '— a file column —' : '— not kept —'], ...tcols.filter((c) => !want || (want === 'file' ? c.type === 'file' : want === 'checklist' ? c.type === 'checklist' : !['file', 'checklist', 'calc'].includes(c.type))).map((c) => [c.key, c.label])], cur || '');
      h += `<div class="bx-row">on the row of ${sel(`sd2:${P}`, [['', '— a table —'], ...tables.map((t) => [t, W.text(B.F.data[t].title) || humanise(t)])], target)}</div>` + rowSel(P, target, s.row, list, i);
      if (s.type === 'action') {
        const jobs = B.ov.actions || [];
        const a = jobs.find((x) => x.name === s.action);
        h += `<div class="bx-row">${sel(`saj:${P}`, [['', '— which job —'], ...jobs.map((x) => [x.name, `${x.group} · ${x.title}`])], s.action)}</div>`;
        if (a) {
          const fr = s.from && s.from.data ? s.from.data : '';
          const fcols = fr ? srcColumns(B, { data: fr }) : tcols;
          const fileSel = (key, cur) => sel(key, [['', '— a file column —'], ...fcols.filter((c) => c.type === 'file').map((c) => [c.key, c.label])], cur || '');
          const statusCol = tcols.find((c) => c.type === 'choice');
          h += `<div class="bx-hint">${esc(a.description)}${a.slow ? ' It runs in the background; the rule carries on when it is done.' : ''}</div>`
            + (a.inputs.some((p) => p.kind === 'file') ? `<div class="bx-row">the files come from ${sel(`sfr:${P}`, [['', 'this row'], ...tables.filter((t) => t !== target).map((t) => [t, 'the row of ' + (W.text(B.F.data[t].title) || humanise(t)) + ' that started it'])], fr)}</div>` : '')
            + a.inputs.map((p) => `<div class="bx-row"><span class="bx-hint" style="min-width:110px">${esc(p.label)}</span>${p.kind === 'file' ? fileSel(`sai:${P}:${p.key}`, (s.in || {})[p.key]) : p.kind === 'choice' ? sel(`sai:${P}:${p.key}`, [['', '— choose —'], ...(p.choices || [])], String((s.in || {})[p.key] ?? '')) : inp(`sait:${P}:${p.key}`, tok((s.in || {})[p.key]), `placeholder="${p.kind === 'month' ? '{answer} or 2026-08' : ''}" style="flex:1"`)}</div>`).join('')
            + `<div class="bx-sub">What comes back goes into</div>`
            + [...a.outputs, ...(((a.outputs_by || {})[String((s.in || {})[(a.inputs.find((x) => x.kind === 'choice') || {}).key] ?? '')]) || [])].map((p) => `<div class="bx-row"><span class="bx-hint" style="min-width:110px">${esc(p.label)}</span>${colSel(`sao:${P}:${p.key}`, p.kind === 'file' ? 'file' : p.kind === 'checklist' ? 'checklist' : '', (s.out || {})[p.key])}</div>`).join('')
            + (statusCol ? `<div class="bx-sub">If it does not finish</div><div class="bx-row">set ${esc(statusCol.label)} to ${sel(`sfv:${P}:${statusCol.key}`, [['', '— leave it —'], ...(statusCol.options || []).map((o) => [o, o])], (s.fail || {})[statusCol.key] || '')} and put why in ${sel(`sfn:${P}`, [['', '— nowhere —'], ...tcols.filter((c) => (c.type || 'text') === 'text').map((c) => [c.key, c.label])], Object.keys(s.fail || {}).find((k) => (s.fail || {})[k] === '{{error}}') || '')}</div>` : '');
        }
      } else {
        const docs = (B.assets || []).filter((x) => /\.docx$/i.test(x.name));
        h += `<div class="bx-row">from ${sel(`smt:${P}`, [['', '— a Word template —'], ...docs.map((x) => [x.name, x.name])], s.template)}<button type="button" class="bx-lnk" data-tplup="${P}">Upload a template…</button></div>`
          + `<div class="bx-row">into ${colSel(`smi:${P}`, 'file', s.into)} named ${inp(`smn:${P}`, tok(s.name), 'placeholder="Agreement - {Site}.docx" style="flex:1"')}</div>`
          + `<div class="bx-hint">In the template, put ${tcols.slice(0, 6).map((c) => `<span class="mono">{{${esc(c.key)}}}</span>`).join(' ')}${tcols.length > 6 ? ' …' : ''} and <span class="mono">{{today}}</span> where the row’s values go.</div>`;
      }
    }
    if (s.type === 'stop') h += `<div class="bx-row">${seg(`so:${P}`, [['done', 'As done'], ['failed', 'As failed']], s.outcome || 'done')}</div>` + inp(`sm:${P}`, s.message, 'placeholder="A sentence for the run log"');
    return `<div class="bx-step">${h}<div class="bx-row"><button type="button" class="bx-lnk" data-sup="${P}">↑</button><button type="button" class="bx-lnk" data-sdn="${P}">↓</button><span style="flex:1"></span><button type="button" class="bx-lnk danger" data-sx="${P}">Remove this step</button></div></div>`;
  };
  host.innerHTML = titleBar
    + Q('', 'Its name', inp('rtitle', W.text(f.title)))
    + `<div class="bx-rstep"><span class="bx-w when">WHEN</span><div class="bx-q">${whenInner}</div></div>`
    + `<div class="bx-rstep"><span class="bx-w rows">ROWS</span><div class="bx-q">${goInner}</div></div>`
    + `<div class="bx-rstep"><span class="bx-w if">IF</span><div class="bx-q">${onlyInner}${!R.only.length ? '<span class="bx-hint">Always — no condition.</span>' : ''}</div></div>`
    + `<div class="bx-rstep"><span class="bx-w then">THEN</span><div class="bx-stack">${R.then.map((s, i) => stepHtml(s, i, 'then')).join('')}<button type="button" class="bx-lnk" data-sadd="then">+ ${R.then.length ? 'then' : 'do something'}</button></div></div>`
    + (R.go && R.go.each ? `<div class="bx-rstep"><span class="bx-w after">AFTER</span><div class="bx-stack"><div class="bx-hint">Once, after every row is done.</div>${R.after.map((s, i) => stepHtml(s, i, 'after')).join('')}<button type="button" class="bx-lnk" data-sadd="after">+ once at the end</button></div></div>` : R.go ? `<div class="bx-rstep"><span class="bx-w after">WITH</span><div class="bx-stack"><div class="bx-hint">The whole list: {count} is how many rows.</div>${R.after.map((s, i) => stepHtml(s, i, 'after')).join('')}<button type="button" class="bx-lnk" data-sadd="after">+ do something</button></div></div>` : '')
    + (rowName || R.go || wh.type === 'button' ? chipsFor() : '')
    + Q('', 'Runs with the rights of', seg('runas', [['starter', 'The person who started it'], ['owner', 'The tool’s owner']], wh.type === 'schedule' ? 'owner' : f.run_as || 'starter'), wh.type === 'schedule' ? 'A schedule always runs as the owner.' : '')
    + `<div class="bx-q"><button type="button" class="btn sm" data-test>Try it — nothing is written or sent</button><div class="bx-test" id="bx-test" hidden></div></div>`
    + `<div class="bx-foot"><span class="bx-hint" style="flex:1">A new rule starts switched off.</span><button type="button" class="bx-lnk danger" data-delrule>Remove this rule</button></div>`;

  // ---- wiring: every change goes into R and is compiled back into the flow --------------------------
  const commit = (redraw = true) => { const c = compile(R); f.trigger = c.trigger; f.start = c.start; f.steps = c.steps; delete f.canvas; if (f.trigger.type === 'schedule') f.run_as = 'owner'; B.mark(redraw); };
  const listOf = (P) => {
    const d = P.match(/^d(then|after)\.(\d+):(\d+)$/);
    if (d) { const host_ = (d[1] === 'after' ? R.after : R.then)[+d[2]]; host_.declined = host_.declined || []; return [host_.declined, +d[3]]; }
    const [l, i] = P.split(':'); return [l === 'after' ? R.after : R.then, +i];
  };
  let lastText = null;
  host.querySelectorAll('input[type=text], textarea').forEach((i) => i.addEventListener('focus', () => (lastText = i)));
  on(host, '[data-tok]', 'click', (b) => { const i = lastText; if (!i) { toast('Click into a message first, then on a word.'); return; } const t = `{${b.dataset.tok === 'count' ? 'count' : b.dataset.tok}}`, a = i.selectionStart ?? i.value.length; i.value = i.value.slice(0, a) + t + i.value.slice(i.selectionEnd ?? a); i.focus(); i.dispatchEvent(new Event('change')); });
  const store = (v) => storeTokens(String(v).replace(/\{count\}/g, R.go ? `{{${R.go.as || 'rows'}_count}}` : '{count}').replace(/\{answer\}/g, '{{trigger.values.answer}}'), rowName, rowCols);
  inps(host, (k, v) => { if (k === 'rtitle') { W.set(f, 'title', `flow.${name}.title`, v); B.mark(false); } });
  inps(host, (k, v) => {
    if (k === 'rtitle') return;
    if (k === 'wat') { wh.at = v; return commit(false); }
    let m = k.match(/^g(v):(\d+)$/); if (m) return; // handled by wireWhere
    m = k.match(/^olt:(\d+):(\d+)$/); if (m) { R.only[+m[1]].conds[+m[2]].left = store(v); return commit(false); }
    m = k.match(/^ov:(\d+):(\d+)$/); if (m) { R.only[+m[1]].conds[+m[2]].value = store(v); return commit(false); }
    m = k.match(/^(st|sx|sto|ssu|sq|swe|sn|sva|sm|sap):([\w.]+:\d+)$/);
    if (m) { const [list, i] = listOf(m[2]); const s = list[i]; const map = { st: 'title', sx: 'text', sto: 'to', ssu: 'subject', sq: 'question', sn: 'name', sva: 'value', sm: 'message', sap: 'path' }; if (m[1] === 'swe') s.who = { ...(s.who || {}), emails: store(v) }; else s[map[m[1]]] = ['sn', 'sm', 'sap'].includes(m[1]) ? v : store(v); return commit(false); }
    m = k.match(/^srlt:([\w.]+:\d+):(\d+)$/); if (m) { const [list, i] = listOf(m[1]); list[i].when[+m[2]].left = store(v); return commit(false); }
    m = k.match(/^srv:([\w.]+:\d+):(\d+)$/); if (m) { const [list, i] = listOf(m[1]); list[i].when[+m[2]].value = store(v); return commit(false); }
    m = k.match(/^srmsg:([\w.]+:\d+)$/); if (m) { const [list, i] = listOf(m[1]); list[i].message = store(v); return commit(false); }
    m = k.match(/^sait:([\w.]+:\d+):(\w+)$/); if (m) { const [list, i] = listOf(m[1]); const st = list[i]; st.in = { ...(st.in || {}) }; if (v === '') delete st.in[m[2]]; else st.in[m[2]] = store(v); return commit(false); }
    m = k.match(/^smn:([\w.]+:\d+)$/); if (m) { const [list, i] = listOf(m[1]); list[i].name = store(v); return commit(false); }
    m = k.match(/^sv:([\w.]+:\d+):(.+)$/); if (m) { const [list, i] = listOf(m[1]); const s = list[i]; s.values = s.values || {}; if (v === '') delete s.values[m[2]]; else s.values[m[2]] = store(v); return commit(false); }
    m = k.match(/^sw:([\w.]+:\d+):(\w+)$/); if (m) { const [list, i] = listOf(m[1]); const s = list[i]; if (+v > 0) s[m[2]] = +v; else delete s[m[2]]; return commit(false); }
  }, 'change');
  segs(host, (k, v) => {
    if (k === 'on') { f.enabled = v === '1'; return B.mark(); }
    if (k === 'wtype') { R.when = { type: v }; if (v === 'schedule') { R.when.every = 'weekday'; R.when.at = '07:00'; } if (v === 'row_added' || v === 'row_changed') R.when.data = tables[0] || ''; return commit(); }
    if (k === 'goeach') { R.go.each = v === '1'; R.go.item = R.go.item || 'row'; if (!R.go.each) { R.then = []; R.only = []; } return commit(); }
    if (k === 'runas') { f.run_as = v; return B.mark(); }
    const m = k.match(/^so:([\w.]+:\d+)$/); if (m) { const [list, i] = listOf(m[1]); list[i].outcome = v; return commit(); }
  });
  sels(host, (k, v) => {
    if (k === 'wdata') { wh.data = v; return commit(); }
    if (k === 'wbrow') { if (v) wh.data = v; else delete wh.data; return commit(); }
    if (k === 'wevery') { wh.every = v; if (v === 'week') wh.day = wh.day || 'mon'; if (v === 'month') wh.day = 1; if (v !== 'week' && v !== 'month') delete wh.day; return commit(); }
    if (k === 'wday') { wh.day = v; return commit(); }
    if (k === 'wmday') { wh.day = +v; return commit(); }
    if (k === 'gosrc') { const [kind, id] = [v.split(':')[0], v.slice(v.indexOf(':') + 1)]; delete R.go.data; delete R.go.sheet; delete R.go.provider; delete R.go.params; R.go[kind] = id; R.go.where = []; return commit(); }
    let m = k.match(/^om:(\d+)$/); if (m) { R.only[+m[1]].match = v; return commit(); }
    m = k.match(/^ol:(\d+):(\d+)$/); if (m) { R.only[+m[1]].conds[+m[2]].left = v ? `{{${rowName}.${v}}}` : ''; return commit(); }
    m = k.match(/^oo:(\d+):(\d+)$/); if (m) { const c = R.only[+m[1]].conds[+m[2]]; c.op = v; if (NO_VALUE.has(v)) delete c.value; return commit(); }
    m = k.match(/^sk:([\w.]+:\d+)$/); if (m) { const [list, i] = listOf(m[1]); list[i] = newStep(v); if (v === 'update_row') list[i].row = rowName ? `{{${rowName}.id}}` : ''; return commit(); }
    m = k.match(/^swk:([\w.]+:\d+)$/); if (m) { const [list, i] = listOf(m[1]); list[i].who = { kind: v, ...(['person', 'manager_of'].includes(v) ? { person: '{{person.id}}' } : {}) }; return commit(); }
    m = k.match(/^swp:([\w.]+:\d+)$/); if (m) { const [list, i] = listOf(m[1]); list[i].who = { ...(list[i].who || {}), person: v }; return commit(); }
    m = k.match(/^sd:([\w.]+:\d+)$/); if (m) { const [list, i] = listOf(m[1]); list[i].data = v; list[i].values = {}; return commit(); }
    m = k.match(/^sac:([\w.]+:\d+)$/); if (m) { const [list, i] = listOf(m[1]); list[i].connection = v; return commit(); }
    m = k.match(/^srm:([\w.]+:\d+)$/); if (m) { const [list, i] = listOf(m[1]); list[i].match = v; return commit(); }
    m = k.match(/^srl:([\w.]+:\d+):(\d+)$/); if (m) { const [list, i] = listOf(m[1]); list[i].when[+m[2]].left = v ? `{{${rowName}.${v}}}` : ''; return commit(); }
    m = k.match(/^sro:([\w.]+:\d+):(\d+)$/); if (m) { const [list, i] = listOf(m[1]); const c = list[i].when[+m[2]]; c.op = v; if (NO_VALUE.has(v)) delete c.value; return commit(); }
    m = k.match(/^srow:([\w.]+:\d+)$/); if (m) { const [list, i] = listOf(m[1]); list[i].row = v; return commit(); }
    m = k.match(/^sfr:([\w.]+:\d+)$/); if (m) { const [list, i] = listOf(m[1]); const st = list[i]; if (v) st.from = { data: v, row: rowName ? `{{${rowName}.id}}` : '' }; else delete st.from; for (const p of ((B.ov.actions || []).find((x) => x.name === st.action) || { inputs: [] }).inputs) if (p.kind === 'file') delete (st.in || {})[p.key]; return commit(); }
    m = k.match(/^sfv:([\w.]+:\d+):(\w+)$/); if (m) { const [list, i] = listOf(m[1]); const st = list[i]; st.fail = { ...(st.fail || {}) }; if (v) st.fail[m[2]] = v; else delete st.fail[m[2]]; if (!Object.keys(st.fail).length) delete st.fail; return commit(); }
    m = k.match(/^sfn:([\w.]+:\d+)$/); if (m) { const [list, i] = listOf(m[1]); const st = list[i]; st.fail = Object.fromEntries(Object.entries(st.fail || {}).filter(([, x]) => x !== '{{error}}')); if (v) st.fail[v] = '{{error}}'; if (!Object.keys(st.fail).length) delete st.fail; return commit(); }
    m = k.match(/^sd2:([\w.]+:\d+)$/); if (m) { const [list, i] = listOf(m[1]); const st = list[i]; st.data = v; st.row = v && v === (wh.data || '') && rowName ? `{{${rowName}.id}}` : st.row || ''; st.in = {}; st.out = {}; delete st.into; return commit(); }
    m = k.match(/^saj:([\w.]+:\d+)$/); if (m) { const [list, i] = listOf(m[1]); const st = list[i]; st.action = v; st.in = {}; st.out = {}; st.data = st.data || (R.go && R.go.data) || wh.data || ''; if (!st.row && rowName) st.row = `{{${rowName}.id}}`; return commit(); }
    m = k.match(/^sai:([\w.]+:\d+):(\w+)$/); if (m) { const [list, i] = listOf(m[1]); const st = list[i]; st.in = { ...(st.in || {}) }; if (v) st.in[m[2]] = v; else delete st.in[m[2]]; return commit(); }
    m = k.match(/^sao:([\w.]+:\d+):(\w+)$/); if (m) { const [list, i] = listOf(m[1]); const st = list[i]; st.out = { ...(st.out || {}) }; if (v) st.out[m[2]] = v; else delete st.out[m[2]]; return commit(); }
    m = k.match(/^smt:([\w.]+:\d+)$/); if (m) { const [list, i] = listOf(m[1]); list[i].template = v; return commit(); }
    m = k.match(/^smi:([\w.]+:\d+)$/); if (m) { const [list, i] = listOf(m[1]); const st = list[i]; st.into = v; st.data = st.data || (R.go && R.go.data) || wh.data || ''; if (!st.row && rowName) st.row = `{{${rowName}.id}}`; return commit(); }
    m = k.match(/^sam:([\w.]+:\d+)$/); if (m) { const [list, i] = listOf(m[1]); list[i].method = v; return commit(); }
  });
  if (R.go) wireWhere(host, R.go.where, 'g', () => commit());
  on(host, '[data-swr]', 'click', (b) => { const parts = b.dataset.swr.split(':'); const r = parts.pop(); const [list0, i0] = listOf(parts.join(':')); const s = list0[i0]; const set = new Set((s.who || {}).roles || []); set.has(r) ? set.delete(r) : set.add(r); s.who = { kind: 'roles', roles: [...set] }; commit(); });
  on(host, '[data-goon]', 'click', () => { R.go = { data: tables[0], where: [], as: 'rows', each: true, item: 'row' }; if (!tables[0] && (B.ov.sheets || [])[0]) { delete R.go.data; R.go.sheet = B.ov.sheets[0].slug; } commit(); });
  on(host, '[data-gooff]', 'click', () => { R.go = null; R.after = []; commit(); });
  on(host, '[data-gadd]', 'click', () => { R.go.where.push({ field: (goCols[0] || {}).key || '', op: 'equals', value: '' }); commit(); });
  on(host, '[data-ogadd]', 'click', () => { R.only.push({ match: 'all', conds: [{ left: '', op: 'equals', value: '' }] }); commit(); });
  on(host, '[data-oadd]', 'click', (b) => { R.only[+b.dataset.oadd].conds.push({ left: '', op: 'equals', value: '' }); commit(); });
  on(host, '[data-ogx]', 'click', (b) => { R.only.splice(+b.dataset.ogx, 1); commit(); });
  on(host, '[data-ox]', 'click', (b) => { const [g, i] = b.dataset.ox.split(':').map(Number); R.only[g].conds.splice(i, 1); if (!R.only[g].conds.length) R.only.splice(g, 1); commit(); });
  on(host, '[data-sadd]', 'click', (b) => { const k = b.dataset.sadd; if (k.startsWith('d')) { const [list0] = listOf(`${k}:0`); list0.push(newStep('notify')); } else (k === 'after' ? R.after : R.then).push(newStep('notify')); commit(); });
  on(host, '[data-sx]', 'click', (b) => { const [list, i] = listOf(b.dataset.sx); list.splice(i, 1); commit(); });
  on(host, '[data-sup]', 'click', (b) => { const [list, i] = listOf(b.dataset.sup); if (i > 0) { [list[i - 1], list[i]] = [list[i], list[i - 1]]; commit(); } });
  on(host, '[data-sdn]', 'click', (b) => { const [list, i] = listOf(b.dataset.sdn); if (i < list.length - 1) { [list[i + 1], list[i]] = [list[i], list[i + 1]]; commit(); } });
  on(host, '[data-sradd]', 'click', (b) => { const [list, i] = listOf(b.dataset.sradd); list[i].when = [...(list[i].when || []), { left: '', op: 'equals', value: '' }]; commit(); });
  on(host, '[data-srx]', 'click', (b) => { const parts = b.dataset.srx.split(':'); const j = +parts.pop(); const [list, i] = listOf(parts.join(':')); list[i].when.splice(j, 1); commit(); });
  on(host, '[data-tplup]', 'click', (b) => B.upload((fname) => { const [list, i] = listOf(b.dataset.tplup); list[i].template = fname; commit(); }, '.docx'));
  on(host, '[data-delrule]', 'click', () => delRule());
  on(host, '[data-test]', 'click', () => B.testRule(name, host.querySelector('#bx-test')));

  async function delRule() {
    if (!(await confirmBox(`Remove the rule “${W.text(f.title) || name}”? Buttons that start it will say so.`, 'Remove'))) return;
    delete B.F.flows[name]; delete B.rules[name]; B.select({ kind: 'page' });
  }
}

// ---- the tool itself ------------------------------------------------------------------------------------
function askTool(host, B, what) {
  const W = words(B), m = B.F.manifest, menu = m.menu || (m.menu = {}), acc = m.access || (m.access = {});
  if (what === 'words') return askWords(host, B);
  if (what === 'about') { host.innerHTML = head('The tool', 'About this tool') + Q('', 'What it is, who uses it, where its data comes from — for whoever looks after it next', area('readme', B.F.readme, 260)); inps(host, (k, v) => { B.F.readme = v; B.mark(false); }); return; }
  if (['release', 'try', 'test', 'deploy'].includes(what)) return B.stepPanel(host, what === 'release' ? 'test' : what);
  host.innerHTML = head('The tool', W.text(m.title) || B.slug)
    + Q(1, 'Its name in the menu', inp('ttitle', W.text(m.title)))
    + Q(2, 'Its picture in the menu', `<div class="bx-chips">${(B.ov.icons || []).map((i) => `<button type="button" data-icon="${esc(i)}" class="${m.icon === i ? 'on' : ''}">${esc(i)}</button>`).join('')}</div>`)
    + Q(3, 'Where in the menu', seg('section', (B.ov.sections || ['My tools', 'Workspace', 'Admin']).map((x) => [x, x]), menu.section || 'My tools'))
    + Q(4, 'Who may use it', `<div class="bx-chips">${['owner', 'admin', 'manager', 'user'].map((r) => `<button type="button" data-trole="${r}" class="${(acc.roles || []).includes(r) ? 'on' : ''}">${humanise(r)}</button>`).join('')}</div><div class="bx-row">…and anyone granted ${sel('grant', [['', '— no grant —'], ...(B.ov.grants || []).map((g) => [g.slug, g.name])], acc.tool || '')}</div><label class="bx-check"><input type="checkbox" data-everyone ${acc.everyone ? 'checked' : ''}> everyone, every role</label>`, 'Grants are given under People & access.')
    + Q(5, 'The person accountable for it', inp('owner', m.owner, 'type="email" placeholder="name@company.com"'), 'Rules on a schedule run with this person’s rights.')
    + Q(6, 'Shown in the menu', seg('hidden', [['0', 'Yes'], ['1', 'No (reached by a link)']], menu.hidden ? '1' : '0'))
    + Q(7, 'Its colour', `<div class="bx-row"><input type="color" data-accent value="${esc(/^#[0-9a-fA-F]{6}$/.test((m.theme || {}).accent || '') ? m.theme.accent : '#0B8A63')}" style="width:44px;height:30px;padding:2px"><button type="button" class="bx-lnk" data-noaccent>CBX-Connect’s own</button></div>`);
  inps(host, (k, v) => { if (k === 'ttitle') W.set(m, 'title', 'tool.title', v); if (k === 'owner') m.owner = v.trim(); B.mark(false); });
  on(host, '[data-icon]', 'click', (b) => { m.icon = b.dataset.icon; B.mark(); });
  segs(host, (k, v) => { if (k === 'section') { menu.section = v; if (v === 'Admin') acc.roles = (acc.roles || []).filter((r) => r === 'owner' || r === 'admin'); } if (k === 'hidden') { if (v === '1') menu.hidden = true; else delete menu.hidden; } B.mark(); });
  on(host, '[data-trole]', 'click', (b) => { const r = b.dataset.trole, set = new Set(acc.roles || []); set.has(r) ? set.delete(r) : set.add(r); acc.roles = ['owner', 'admin', 'manager', 'user'].filter((x) => set.has(x)); B.mark(); });
  sels(host, (k, v) => { if (k === 'grant') { if (v) acc.tool = v; else delete acc.tool; B.mark(); } });
  on(host, '[data-everyone]', 'change', (c) => { if (c.checked) acc.everyone = true; else delete acc.everyone; B.mark(); });
  on(host, '[data-accent]', 'change', (i) => { m.theme = { ...(m.theme || {}), accent: i.value }; B.mark(); });
  on(host, '[data-noaccent]', 'click', () => { if (m.theme) delete m.theme.accent; B.mark(); });
}

function askWords(host, B) {
  const W = words(B), L = B.F.languages;
  const keys = Object.keys(W.en).sort();
  const LANGS = [['ar', 'العربية'], ['pt', 'Português'], ['fr', 'Français']];
  const missing = (l) => keys.filter((k) => W.en[k] && !(L[l] || {})[k]).length;
  const cur = B.wordsLang || 'ar';
  host.innerHTML = head('The tool', 'Words in four languages') + `<p class="bx-hint">English is what you type everywhere else. Here: the other three. Empty shows the English.</p>`
    + seg('wlang', LANGS.map(([k, n]) => [k, `${n}${missing(k) ? ` · ${missing(k)} missing` : ' ✓'}`]), cur)
    + (missing(cur) ? `<div class="bx-row"><button type="button" class="btn sm" data-wtr>Let C-EXA translate the ${missing(cur)} missing</button><span class="bx-hint">You read them before you save.</span></div>` : '')
    + `<div class="bx-words">${keys.filter((k) => W.en[k]).map((k) => `<label class="bx-word"><span>${esc(W.en[k])}</span><input class="bx-in" data-wk="${esc(k)}" dir="${cur === 'ar' ? 'rtl' : 'ltr'}" value="${esc((L[cur] || {})[k] || '')}"></label>`).join('')}</div>`;
  segs(host, (k, v) => { B.wordsLang = v; askWords(host, B); });
  on(host, '[data-wk]', 'input', (i) => { L[cur] = L[cur] || {}; if (i.value) L[cur][i.dataset.wk] = i.value; else delete L[cur][i.dataset.wk]; B.mark(false); });
  on(host, '[data-wtr]', 'click', async (b) => {
    const want = Object.fromEntries(keys.filter((k) => W.en[k] && !(L[cur] || {})[k]).map((k) => [k, W.en[k]]));
    b.disabled = true; b.textContent = 'Translating…';
    try {
      const r = await api.post('/api/builder/translate', { lang: cur, words: want });
      L[cur] = { ...(L[cur] || {}), ...(r.words || {}) };
      toast(`${Object.keys(r.words || {}).length} of ${r.asked || Object.keys(want).length} filled in — read them, then save`);
      B.mark(false); askWords(host, B);
    } catch (e) { toast(e.detail || e.message, 'err'); b.disabled = false; b.textContent = 'Try again'; }
  });
}

export { api };
