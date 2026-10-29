// One renderer for every tool folder. #/tool?t=<slug>&p=<page> asks the engine for the page — its
// layout (sections → columns → blocks), the tool's words in the person's language, and what each block
// may do — and draws it. Blocks: heading, text, stats, table, grid, form, image, embed, code, links,
// divider, spacer. Nothing here is about any one tool. The Builder's canvas draws the same blocks with
// `renderWidget`, pointed at its draft endpoints.
import { api } from '../api.js';
import { esc, toast, modal, confirmBox, pill, md, clip, statusPill } from '../ui.js';
import { lang } from '../lang.js';

const DOT_TITLE = { ok: 'status.ok', warn: 'status.expiring', bad: 'status.expired', open: 'status.missing', grey: 'status.not_required', gold: 'status.no_expiry' };
const STATUS_DOT = { ok: 'ok', expiring: 'warn', expired: 'bad', missing: 'open', needs_date: 'open', not_required: 'grey', no_expiry: 'gold' };
const TILE_COLOUR = { expired: 'var(--crit)', expiring: 'var(--warn)', in_date: 'var(--good)', outstanding: 'var(--muted)' };

export default {
  title: 'Tool',
  navRoute: 'tool',
  async render(root, ctx) {
    const slug = ctx.params.t || '', pageName = ctx.params.p || '';
    const L = lang.get();
    let d;
    try { d = await api.get(`/api/engine/tools/${encodeURIComponent(slug)}/pages/${encodeURIComponent(pageName || 'home')}?lang=${L}`); }
    catch (e) {
      if (e.status === 404 && !pageName) {
        try { const m = await api.get(`/api/engine/menu?lang=${L}`); const t = (m.tools || []).find((x) => x.slug === slug); if (t && t.home) { ctx.go('tool', { t: slug, p: t.home }); return; } } catch { /* fall through */ }
      }
      throw e;
    }
    const S = (k) => (k && d.strings[k]) || k || '';
    root.dir = d.dir;
    root.classList.add('eng');
    applyTheme(root, d.tool.theme);
    document.title = `${S(d.page.title)} · ${S(d.tool.title)} · ${ctx.me && ctx.me.product ? ctx.me.product : 'CBX-Connect'}`;
    const pages = (d.tool.pages || []).filter((p) => p.menu !== false);
    // 1.0.83: a tool's pages are a row of tabs like every other page's (underlined, above the title)
    const tabs = pages.length > 1 ? `<div class="hub-bar eng-tabs" role="tablist">${pages.map((p) => `<button role="tab" aria-selected="${p.name === d.page.name}" class="${p.name === d.page.name ? 'on' : ''}" data-page="${esc(p.name)}">${esc(p.title)}</button>`).join('')}</div>` : '';
    // 1.0.85: a page with a grid gets the whole width — a grid of 20+ document types never fits in 1 400 px
    const hasGrid = JSON.stringify((d.page.layout || d.page) || {}).includes('"type":"grid"');
    const width = ((d.page.style || {}).width || 'normal') === 'normal' && hasGrid ? 'wide' : ((d.page.style || {}).width || 'normal');
    root.innerHTML = `
      ${tabs}<div class="head"><div><h1>${esc(S(d.tool.title))}${pages.length > 1 ? ` <span class="muted" style="font-weight:400">· ${esc(S(d.page.title))}</span>` : ''}</h1>${d.page.intro ? `<p>${esc(S(d.page.intro))}</p>` : ''}</div></div>
      <div id="eng-body" class="eng-page w-${esc(width)}"></div>`;
    root.querySelectorAll('[data-page]').forEach((b) => (b.onclick = () => ctx.go('tool', { t: slug, p: b.dataset.page })));
    // the page's own address may narrow its blocks (1.0.63): #/tool?t=…&p=company&company=5
    const pageParams = Object.fromEntries(Object.entries(ctx.params || {}).filter(([k]) => !['t', 'p', 'open', 'lang'].includes(k)));
    const pq = new URLSearchParams(pageParams).toString();
    const env = {
      slug, page: d.page.name, S, strings: d.strings, me: ctx.me, params: pageParams, go: (link) => { if (link && link.route) ctx.go(link.route, link.params || {}); else if (link && link.url) window.open(link.url, '_blank', 'noopener'); },
      info: d.widgets || {},
      rows: (w, qs) => api.get(`/api/engine/tools/${slug}/data/rows?page=${encodeURIComponent(d.page.name)}&widget=${encodeURIComponent(w.id)}${pq ? '&' + pq : ''}${qs ? '&' + qs : ''}`),
      row: (block, id) => api.get(`/api/engine/tools/${slug}/data/${block}/row/${id}`),
      mail: { info: (block, id, w, i) => api.get(`/api/engine/tools/${slug}/data/${block}/${id}/mail?page=${encodeURIComponent(d.page.name)}&widget=${encodeURIComponent(w.id)}&action=${i}`),
        send: (block, id, body) => api.post(`/api/engine/tools/${slug}/data/${block}/${id}/mail`, { page: d.page.name, ...body }) },
      block: d.widgets,
      assetUrl: (name) => `/api/engine/tools/${slug}/assets/${encodeURIComponent(name)}`,
      codeUrl: (w) => `/api/engine/tools/${slug}/html/${encodeURIComponent(d.page.name)}/${encodeURIComponent(w.id)}`,
      metrics: (keys) => api.get('/api/engine/metrics?keys=' + encodeURIComponent(keys.join(','))),
      runFlow: (flow, values, extra) => api.post(`/api/flows/${slug}/${encodeURIComponent(flow)}/run`, { values: values || {}, ...(extra || {}) }),
      people: (block, col) => api.get(`/api/engine/tools/${slug}/data/${block}/people/${encodeURIComponent(col)}`),
      write: { post: (block, values) => api.post(`/api/engine/tools/${slug}/data/${block}`, { values }), patch: (block, id, values) => api.patch(`/api/engine/tools/${slug}/data/${block}/${id}`, { values }), del: (block, id) => api.del(`/api/engine/tools/${slug}/data/${block}/${id}`),
        upload: (block, id, col, file) => api.upload(`/api/engine/tools/${slug}/data/${block}/${id}/files/${col}`, [file]), unfile: (block, id, col) => api.del(`/api/engine/tools/${slug}/data/${block}/${id}/files/${col}`) },
      choices: (block, col) => api.get(`/api/engine/tools/${slug}/data/${block}/choices/${encodeURIComponent(col)}`),
      fileUrl: (block, id, col) => `/api/engine/tools/${slug}/data/${block}/${id}/files/${encodeURIComponent(col)}`,
    };
    // after a change (a row saved, a button on a row), every part of the page shows the rows as they are now
    env.refresh = () => renderLayout(root.querySelector('#eng-body'), d.page.layout, env);
    renderLayout(root.querySelector('#eng-body'), d.page.layout, env);
    // #/tool?…&open=12 (a link in a mail): open that row of the page's first table
    if (ctx.params.open) {
      const blockName = Object.values(d.widgets || {}).map((i) => i.block && i.block.name).find(Boolean);
      if (blockName) openById(env, blockName, +ctx.params.open);
    }
  },
};

export function applyTheme(host, theme) {
  if (!host) return;
  const t = theme || {};
  if (t.accent && /^#[0-9a-fA-F]{6}$/.test(t.accent)) host.style.setProperty('--accent', t.accent); else host.style.removeProperty('--accent');
  host.classList.toggle('dense', t.density === 'compact');
}

/** Draw a layout (sections → columns → blocks) into `host`. */
// a choice's words: opt.<table>.<column>.<value>, the value itself when the tool has none
export const optSlug = (v) => String(v).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40) || 'x';
const optLabel = (S, block, col, v) => { const k = `opt.${block}.${col}.${optSlug(v)}`; const t = S(k); return t && t !== k ? t : String(v); };
// the rows each link column may point at, fetched once for a form or a filter
async function linkChoices(env, block, cols) {
  const out = {};
  await Promise.all(cols.filter((c) => c.type === 'link').map(async (c) => { try { out[c.key] = env.choices ? (await env.choices(block, c.key)).choices || [] : null; } catch { out[c.key] = []; } }));
  await Promise.all(cols.filter((c) => c.type === 'person').map(async (c) => { try { out[c.key] = env.people ? (await env.people(block, c.key)).choices || [] : null; } catch { out[c.key] = []; } }));
  return out;
}
// a checklist field: its items as ticks and words, "+ a step" adds one (1.0.54)
function wireChecklists(root) {
  root.querySelectorAll('[data-checklist]').forEach((box) => {
    const add = box.querySelector('[data-ck-add]');
    if (add) add.onclick = (e) => { e.preventDefault(); const row = document.createElement('div'); row.className = 'eng-ck'; row.innerHTML = '<input type="checkbox"><input type="text" placeholder="A step">'; box.insertBefore(row, add); row.querySelector('input[type=text]').focus(); };
  });
}
const ckItems = (v) => (Array.isArray(v) ? v : []);
const ckSummary = (v) => { const it = ckItems(v); if (!it.length) return '<span class="muted">—</span>'; const d = it.filter((x) => x.done).length; return `<span class="eng-ckbar" title="${d} of ${it.length}"><i style="width:${Math.round((100 * d) / it.length)}%"></i></span> <span class="small num">${d}/${it.length}</span>`; };
const named = (row, cols, me) => (cols || []).some((c) => c.type === 'person' && row && me && row[c.key] === me.id);

/** A button on a row: asks what it must first (a date, a line), then starts its rule with the row. */
async function runRowAction(env, a, block, rid, where, after) {
  const S = env.S, U = lang.ui;
  if (!env.runFlow) { toast('Preview: the button runs on the live page.'); return; }
  let values = {};
  if ((a.ask || []).length) {
    values = await new Promise((res) => {
      const m = modal({ title: S(a.label), body: `<form class="eng-form">${a.ask.map((q) => `<div class="field"><label>${esc(S(q.label))}</label>${q.type === 'choice' ? `<select name="${esc(q.key)}">${(q.options || []).map((o) => `<option value="${esc(o.value)}">${esc(S(o.label))}</option>`).join('')}</select>` : `<input name="${esc(q.key)}" type="${q.type === 'date' ? 'date' : q.type === 'month' ? 'month' : 'text'}" ${q.type === 'month' ? 'placeholder="2026-08"' : ''} required>`}</div>`).join('')}</form>`, footer: `<button class="btn" data-close>${esc(U('cancel'))}</button><button class="btn primary" data-ok>${esc(S(a.label))}</button>`, onClose: () => res(null) });
      m.root.querySelector('[data-ok]').onclick = () => { const f = m.root.querySelector('form'); if (!f.reportValidity()) return; const v = Object.fromEntries(a.ask.map((q) => [q.key, f.elements[q.key].value])); m.root.remove(); res(v); };
    });
    if (!values) return;
  } else if (a.confirm && !(await confirmBox(S(a.confirm), S(a.label)))) return;
  const done = () => { if (env.refresh) env.refresh(); else if (after) after(); };
  try {
    const r = await env.runFlow(a.flow, values, { block, row: rid, ...where }); const st = r.run.status;
    const working = st === 'waiting' && (r.run.waiting || {}).kind === 'working';
    toast(st === 'done' ? (r.run.note || U('saved')) : working ? `${(r.run.waiting || {}).question || 'Working on it'} — the row fills in when it is done` : st === 'waiting' ? 'Sent for approval' : (r.run.note || st), st === 'failed' ? 'err' : '');
    done();
    if (working) watchRun(r.run.id, done);
  } catch (e) { toast(e.detail || e.message, 'err'); }
}

// a job running in the background: look every few seconds (for up to 15 minutes), then show the rows again
function watchRun(id, done, tries = 0) {
  if (tries > 300) return;
  setTimeout(async () => {
    try {
      const r = (await api.get(`/api/flows/runs/${id}`)).run;
      if (r.status === 'waiting' && (r.waiting || {}).kind === 'working') return watchRun(id, done, tries + 1);
      toast(r.status === 'failed' ? `Did not finish: ${r.note || ''}` : (r.note || 'Done'), r.status === 'failed' ? 'err' : '');
      done();
    } catch { /* signed out or gone: stop looking */ }
  }, tries < 10 ? 2000 : 5000);
}

// Progress (1.0.55): each row of a table (a goal) with a bar — how many rows count towards it, of its target
async function renderProgress(el, w, env, title) {
  const S = env.S;
  const info = (env.info || {})[w.id] || {};
  const block = info.block || { columns: [] };
  el.innerHTML = `<div class="card">${title}<div class="bd"><span class="spin"></span></div></div>`;
  const load = async () => {
    try {
      const r = await env.rows(w, '');
      const cols = r.columns || [];
      const labelCol = w.label || (cols.find((c) => (c.type || 'text') === 'text') || {}).key;
      const acts = (w.actions || []).filter((a) => a && a.flow);
      const extra = (w.fields || []).map((f) => (typeof f === 'string' ? f : f.key));
      const items = (r.rows || []).map((g) => {
        const p = g._progress || { count: 0, target: 0 };
        const pc = p.target ? Math.min(100, Math.round((100 * p.count) / p.target)) : 0;
        const bits = extra.map((k) => { const c = cols.find((x) => x.key === k) || {}; const v = g[k]; if (v === null || v === undefined || v === '') return ''; return `<span>${esc(S(c.label) || k)}: ${c.type === 'date' ? esc(lang.date(v)) : c.type === 'person' ? esc((g._labels || {})[k] || '') : esc(String(v))}</span>`; }).filter(Boolean).join(' · ');
        return `<div class="eng-goal"><div class="eng-goal-h"><b>${esc(String(g[labelCol] ?? '#' + g.id))}</b><span class="num">${esc(lang.num(p.count))} / ${esc(lang.num(p.target))}</span></div><div class="eng-bar-t"><i style="width:${pc}%"></i></div>${bits ? `<div class="small muted">${bits}</div>` : ''}${acts.map((a, i) => (!a.reached || p.reached ? `<button class="btn sm" data-gact="${i}:${g.id}">${esc(S(a.label))}</button>` : '')).join('')}</div>`;
      }).join('');
      el.innerHTML = `<div class="card">${title}<div class="bd eng-goals">${items || `<div class="empty">${esc(lang.ui('no_rows'))}</div>`}</div></div>`;
      el.querySelectorAll('[data-gact]').forEach((b) => (b.onclick = async () => { const [i, id] = b.dataset.gact.split(':'); await runRowAction(env, acts[+i], block.name, +id, { page: env.page, widget: w.id }, load); }));
    } catch (e) { el.innerHTML = `<div class="card"><div class="bd err-box">${esc(e.detail || e.message)}</div></div>`; }
  };
  await load();
}

/** The dialog to add or change one row of the tool's own table — the same for a table and a board. */
// money (a number column shown as money): R 1 250.00, as South Africa writes it
export const rand = (v) => { const n = Number(v); if (!isFinite(n)) return String(v); const [w, d] = Math.abs(n).toFixed(2).split('.'); return `${n < 0 ? '−' : ''}R ${w.replace(/\B(?=(\d{3})+(?!\d))/g, ' ')}.${d}`; };

// a new row starts with its columns' starting values (today, me, a pick-list option) and what the block presets
export function startValues(env, cols, preset = {}) {
  const out = {};
  for (const c of cols || []) {
    const d = c.default;
    if (d === undefined || d === null || d === '') continue;
    out[c.key] = d === 'today' && c.type === 'date' ? new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10) : d === 'me' && c.type === 'person' ? (env.me || {}).id : d;
  }
  return { ...out, ...(preset || {}) };
}

// one row by its number: fetched (the server checks the person may see it), then its form (1.0.63)
export async function openById(env, blockName, id, after) {
  if (!env.row) return;
  try { const r = await env.row(blockName, id); openRowForm(env, { ...r.block, name: blockName }, r.row, after, {}, r.can); }
  catch (e) { toast(e.detail || e.message, 'err'); }
}

// Mail about a row (1.0.63): to someone CBX knows, with a link back to the row; the outbox keeps it
async function mailDialog(env, w, idx, blockName, rid) {
  if (!env.mail) { toast('Preview: Mail works on the live page.'); return; }
  let info;
  try { info = await env.mail.info(blockName, rid, w, idx); } catch (e) { toast(e.detail || e.message, 'err'); return; }
  const sug = info.suggested;
  const opts = (info.recipients || []).map((r) => `<option value="${esc(r.email)}">${esc(r.name)} — ${esc(r.email)}${r.entity ? ` (${esc(r.entity)})` : ''}</option>`).join('');
  const body = `<form class="eng-form">
    ${info.mail_ready ? '' : `<p class="small" style="color:var(--warn)">${esc(info.mail_note || 'The mail server is not set up — it will be delivered inside CBX-Connect only.')}</p>`}
    <div class="field"><label>To</label><input type="text" name="to" list="eng-mto" value="${esc(sug ? sug.email : '')}" placeholder="name@company" autocomplete="off"><datalist id="eng-mto">${opts}</datalist>${sug ? `<span class="small muted">${esc(sug.name)}</span>` : ''}</div>
    <div class="field"><label>Message</label><textarea name="message" rows="5" required></textarea></div>
    ${(info.about || []).length ? `<div class="small muted">With it: ${info.about.map(esc).join(' · ')} — and a link straight to it.</div>` : ''}
    ${(info.sent || []).length ? `<div class="small muted" style="margin-top:8px">Sent before: ${info.sent.slice(0, 3).map((x) => `${esc(x.to)} · ${esc(lang.date(x.at))}${x.ok ? '' : ' (not sent)'}`).join('; ')}</div>` : ''}</form>`;
  const m = modal({ title: 'Mail', body, footer: `<button class="btn" data-close>${esc(lang.ui('cancel'))}</button><button class="btn primary" data-send>Send</button>` });
  m.root.querySelector('[data-send]').onclick = async () => {
    const f = m.root.querySelector('form'); if (!f.reportValidity()) return;
    try { const r = await env.mail.send(blockName, rid, { widget: w.id, action: idx, to: f.elements.to.value, message: f.elements.message.value }); m.close(); toast(r.message, r.ok ? '' : 'err'); } catch (e) { toast(e.detail || e.message, 'err'); }
  };
}

export async function openRowForm(env, block, row, after, preset = {}, canRow = null) {
  const S = env.S, U = lang.ui;
  const bc = (block.columns || []).filter((c) => c.type !== 'calc');
  if (!row) preset = startValues(env, bc, preset);
  const ch = await linkChoices(env, block.name, bc);
  const fields = bc.map((c) => field(c, row ? row[c.key] : preset[c.key], S, U, { block: block.name, links: ch, url: row && env.fileUrl ? env.fileUrl(block.name, row.id, c.key) : '' })).join('');
  const ro = !!(row && canRow && canRow.this === false);
  // 1.0.83: the title says what it is — "Add to Tasks", "Edit — Upload April statements" — not "New row" / "#2"
  const named = row ? bc.map((c) => row[c.key]).find((v) => typeof v === 'string' && v.trim()) : '';
  const what = block.title ? S(block.title) : '';
  const title = row ? (ro ? clip(named || what, 60) : `${U('edit')}${named ? ' — ' + clip(named, 50) : ''}`) : (what ? `${U('add_to')} ${what}` : U('new_row'));
  const m = modal({ title, body: `<form class="eng-form" ${ro ? 'inert' : ''}>${fields}</form>`, footer: ro ? `<button class="btn" data-close>${esc(U('cancel'))}</button>` : `<button class="btn" data-close>${esc(U('cancel'))}</button><button class="btn primary eng-save">${esc(U('save'))}</button>` });
  wireChecklists(m.root);
  if (ro) return;
  m.root.querySelector('.eng-save').onclick = async () => {
    const form = m.root.querySelector('form');
    // 1.0.106: a required box left empty is named here, before the server answers "'company' is required"; a pick list
    // with nothing in it says so (Entity Compliance: no company yet). Files are left to the server (their input is hidden).
    const miss = bc.filter((c) => c.required && c.type !== 'file').map((c) => [c, form.elements[c.key]])
      .find(([, x]) => x && x.tagName && !x.disabled && !String(x.value || '').trim());
    if (miss) {
      const [c, x] = miss, name = S(c.label || c.key);
      toast(U(x.tagName === 'SELECT' && x.options.length <= 1 ? 'none_to_pick' : 'fill_in').replace('{name}', name), 'err');
      try { x.focus(); } catch { /* not focusable */ }
      return;
    }
    const values = readForm(form, bc);
    try { const saved = row ? await env.write.patch(block.name, row.id, values) : await env.write.post(block.name, values); const up = await saveFiles(env, block.name, (saved || row).id, form, bc); m.close(); toast(U('saved')); if (env.refresh) env.refresh(); else if (after) after(); watchSaved(env, [saved, ...up]); } catch (e) { toast(e.detail || e.message, 'err'); }
  };
}
// after a row is saved: the files chosen on the form go up, the ones ticked off come away
async function saveFiles(env, block, id, form, cols) {
  const out = [];
  for (const c of cols.filter((x) => x.type === 'file')) {
    const f = form.elements[c.key], rm = form.elements[c.key + '__remove'];
    if (f && f.files && f.files.length) out.push(await env.write.upload(block, id, c.key, f.files[0]));
    else if (rm && rm.checked) out.push(await env.write.unfile(block, id, c.key));
  }
  return out;
}
// a save that started a job in the background: say so, and show the rows again when it is done
function watchSaved(env, results) {
  const ids = [...new Set((results || []).flatMap((r) => (r && r._working) || []))];
  if (!ids.length) return;
  toast('Working on it in the background — the row fills in when it is done');
  for (const id of ids) watchRun(id, () => env.refresh && env.refresh());
}

export function renderLayout(host, layout, env) {
  const secs = (layout && layout.sections) || [];
  host.innerHTML = secs.map((s, si) => `<section class="eng-sec" data-sec="${si}">${s.title ? `<h2 class="eng-sec-title">${esc(env.S(s.title))}</h2>` : ''}<div class="eng-cols">${(s.columns || []).map((c, ci) => `<div class="eng-col" style="grid-column:span ${Math.max(1, Math.min(12, c.width || 12))}" data-col="${ci}">${(c.widgets || []).map((w) => `<div class="eng-w eng-w-${esc(w.type)}" data-w="${esc(w.id)}"></div>`).join('')}</div>`).join('')}</div></section>`).join('') || `<div class="card"><div class="bd empty">${esc(lang.ui('no_rows'))}</div></div>`;
  for (const s of secs) for (const c of s.columns || []) for (const w of c.widgets || []) {
    const el = host.querySelector(`.eng-w[data-w="${cssEscape(String(w.id))}"]`);
    if (el) renderWidget(el, w, env);
  }
}
const cssEscape = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : s.replace(/["\\]/g, '\\$&'));

/** Draw one block into `el`. `env` gives words, data and links; the Builder passes its own. */
export async function renderWidget(el, w, env) {
  const S = env.S, U = lang.ui;
  const title = w.title ? `<div class="hd"><h2>${esc(S(w.title))}</h2><span class="eng-w-extra"></span></div>` : '';
  try {
    switch (w.type) {
      case 'heading': { const lvl = Math.min(3, Math.max(1, +w.level || 2)); el.innerHTML = `<h${lvl} class="eng-h${lvl}">${esc(S(w.text))}</h${lvl}>`; return; }
      case 'text': el.innerHTML = `<div class="eng-text ${w.tone === 'muted' ? 'muted' : ''}">${md(S(w.text))}</div>`; return;
      case 'divider': el.innerHTML = '<hr class="eng-hr">'; return;
      case 'spacer': el.innerHTML = `<div style="height:${Math.max(4, Math.min(200, +w.height || 24))}px"></div>`; return;
      case 'image': {
        const wd = w.width ? `width:${/^\d+%$/.test(w.width) ? w.width : parseInt(w.width, 10) + 'px'};` : 'max-width:100%;';
        el.innerHTML = `<figure class="eng-img align-${esc(w.align || 'left')}"><img src="${esc(env.assetUrl(w.asset))}" alt="${esc(S(w.alt || ''))}" style="${wd}">${w.caption ? `<figcaption class="small muted">${esc(S(w.caption))}</figcaption>` : ''}</figure>`; return;
      }
      case 'embed': {
        el.innerHTML = `<div class="card eng-embed">${title}<iframe src="${esc(w.url)}" title="${esc(S(w.title || 'embed'))}" style="height:${Math.max(120, Math.min(2000, +w.height || 600))}px" loading="lazy" referrerpolicy="no-referrer" sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe><div class="small muted eng-embed-note">${esc(w.url)} · <a href="${esc(w.url)}" target="_blank" rel="noopener">${esc(U('open'))}</a>${w.note ? ` · ${esc(S(w.note))}` : ''}</div></div>`; return;
      }
      case 'code': {
        el.innerHTML = `<div class="card eng-code">${title}<iframe src="${esc(env.codeUrl(w))}" title="code" style="height:${Math.max(80, Math.min(2000, +w.height || 320))}px" sandbox="allow-scripts allow-forms"></iframe></div>`; return;
      }
      case 'links': {
        // 1.0.99: an item pointing at /api/… is a download CBX itself serves (Entity Compliance's Export): same tab, saved as a file
        const own = (it) => /^\/api\//.test(it.url || '');
        const items = (w.items || []).map((it) => { const href = it.url ? esc(it.url) : it.route ? `#/${esc(it.route)}${it.params ? '?' + esc(new URLSearchParams(it.params).toString()) : ''}` : '#'; return `<a class="${w.kind === 'buttons' ? 'btn' + (it.primary ? ' primary' : '') : 'eng-link'}" href="${href}" ${own(it) ? 'download' : it.url ? 'target="_blank" rel="noopener"' : ''}>${esc(S(it.label || it.url || it.route))}</a>`; }).join('');
        // 1.0.99: align "end" puts the buttons on the far side (right, or left in Arabic)
        el.innerHTML = w.kind === 'buttons' ? `<div class="row eng-links" style="gap:8px;flex-wrap:wrap${w.align === 'end' ? ';justify-content:flex-end' : ''}">${items}</div>` : `<div class="card">${title}<div class="bd eng-links-list">${items}</div></div>`; return;
      }
      case 'metrics': return renderMetrics(el, w, env);
      case 'button': {
        // a Run button: starts one of the tool's flows (the server checks who may, and runs it with their rights)
        const f = (env.info[w.id] || {}).flow || { name: w.flow, title: w.flow, enabled: true };
        el.innerHTML = `<div class="row eng-run" style="gap:10px;align-items:center"><button class="btn ${w.primary === false ? '' : 'primary'}" data-run ${f.enabled === false ? 'disabled title="This flow is switched off"' : ''}>${esc(S(w.label) || f.title || 'Run')}</button><span class="small muted" data-run-out></span></div>`;
        const b = el.querySelector('[data-run]'), out = el.querySelector('[data-run-out]');
        b.onclick = async () => {
          if (!env.runFlow) { toast('Preview: the button runs on the live page (use Test on the Flows tab).'); return; }
          if (w.confirm && !(await confirmBox(S(w.confirm), S(w.label) || 'Run'))) return;
          b.disabled = true; out.textContent = '…';
          try {
            const r = await env.runFlow(w.flow);
            const st = r.run.status;
            out.innerHTML = st === 'done' ? `Done${r.run.note ? ' — ' + esc(r.run.note) : ''}` : st === 'waiting' ? `Waiting — ${esc((r.run.waiting || {}).question || 'for an approval')}` : st === 'failed' ? `<span style="color:var(--crit)">Did not finish: ${esc(r.run.note)}</span>` : esc(st);
          } catch (e) { out.innerHTML = `<span style="color:var(--crit)">${esc(e.detail || e.message)}</span>`; }
          finally { b.disabled = f.enabled === false; }
        };
        return;
      }
      case 'stats': return renderStats(el, w, env, title);
      case 'grid': return renderGrid(el, w, env, title);
      case 'table': return w.show === 'totals' ? renderTotals(el, w, env, title) : renderTable(el, w, env, title);
      case 'chart': return renderChart(el, w, env, title);
      case 'board': return renderBoard(el, w, env, title);
      case 'progress': return renderProgress(el, w, env, title);
      case 'form': return renderForm(el, w, env, title);
      default: el.innerHTML = `<div class="err-box">Unknown block '${esc(w.type)}'.</div>`;
    }
  } catch (e) { el.innerHTML = `<div class="card"><div class="bd err-box">${esc(e.detail || e.message || e)}</div></div>`; }
}

// Numbers: a row of tiles, one metric each (cbx/engine/metrics.py). The server leaves out any number
// the person may not read, so a tile the person has no right to simply is not there. On Home a person
// may choose their own tiles for a block (env.tilesFor).
export async function renderMetrics(el, w, env) {
  const S = env.S;
  const items = (w.items || []).filter((it) => it && it.metric);
  const own = env.tilesFor ? env.tilesFor(w) : null;
  const keys = own || items.map((it) => it.metric);
  const labelOf = Object.fromEntries(items.map((it) => [it.metric, it.label]));
  el.innerHTML = `${w.title ? `<div class="eng-stats-title small eyebrow">${esc(S(w.title))}</div>` : ''}<div class="dash-tiles"><div class="loading"><span class="spin"></span></div></div>`;
  const host = el.querySelector('.dash-tiles');
  if (!keys.length) { host.innerHTML = `<div class="small muted">${esc(env.editing ? 'No numbers here yet — add one.' : '')}</div>`; if (env.onTiles) env.onTiles(el, w, []); return; }
  const r = env.metrics ? await env.metrics(keys) : { values: {} };
  const vals = r.values || {};
  const shown = keys.filter((k) => vals[k]);
  host.innerHTML = shown.map((k) => { const v = vals[k]; const tag = v.route || env.editing ? 'button type="button"' : 'div'; return `<${tag} class="card dash-tile ${v.route || env.editing ? '' : 'static'} ${v.tone ? 'tone-' + esc(v.tone) : ''}" data-metric="${esc(k)}" ${v.route ? `data-route="${esc(v.route)}"` : ''}>
    <span class="eyebrow">${esc(labelOf[k] ? S(labelOf[k]) : v.label)}</span><span class="v">${v.value === null || v.value === undefined ? '—' : lang.num(v.value)}</span>${v.sub ? `<span class="small muted">${esc(v.sub)}</span>` : ''}</${tag.split(' ')[0]}>`; }).join('') || `<div class="small muted">${esc(env.editing ? 'No numbers here yet — add one.' : '')}</div>`;
  host.querySelectorAll('.dash-tile').forEach((b) => (b.onclick = () => { if (env.editing) return; if (b.dataset.route) env.go({ route: b.dataset.route }); }));
  // 1.0.83: even rows — 7 tiles are 4 + 3, not 6 and one alone
  const n = shown.length + (env.editing ? 1 : 0), rowsN = Math.ceil(n / 6);
  if (n > 1) host.style.setProperty('--cols', String(Math.ceil(n / rowsN)));
  if (env.onTiles) env.onTiles(el, w, shown);
}

async function renderStats(el, w, env, title) {
  const S = env.S;
  el.innerHTML = `<div class="loading"><span class="spin"></span></div>`;
  const g = await env.rows(w);
  const items = (w.items && w.items.length ? w.items : Object.keys(g.summary || {}).map((k) => ({ key: k }))).slice(0, 8);
  const val = (k) => (g.summary ? g.summary[k] : (k === 'total' || k === 'count' ? g.total : undefined));
  el.innerHTML = `${title ? `<div class="eng-stats-title small eyebrow">${esc(S(w.title))}</div>` : ''}<div class="grid eng-stats" style="grid-template-columns:repeat(${items.length || 1},1fr)">${items.map((it) => `<div class="card stat"><div class="eyebrow">${esc(S(it.label || 'summary.' + it.key))}</div><div class="v" style="color:${val(it.key) && TILE_COLOUR[it.key] ? TILE_COLOUR[it.key] : 'inherit'}">${lang.num(val(it.key) ?? 0)}</div></div>`).join('')}</div>`;
}

async function renderGrid(el, w, env, title) {
  const S = env.S, U = lang.ui;
  el.innerHTML = `<div class="loading"><span class="spin"></span></div>`;
  const g = await env.rows(w);
  const own = !!g.cells; // 1.0.63: a grid from the tool's own tables — a dot opens (or starts) its row
  const extra = g.extra || [];
  let q = '', st = '', who = '';
  const legend = (g.legend || []).map((l) => `<span><i class="dw-dot ${esc(l.dot)}"></i>${esc(S(l.label))}</span>`).join('');
  const head = `<thead><tr><th class="co">${esc(S(w.rows && w.rows.title ? w.rows.title : 'col.entity'))}</th>${extra.map((x) => `<th class="co-x">${esc(S(x.label))}</th>`).join('')}${g.columns.map((c) => `<th class="rot" title="${esc(c.title || c.label)}"><span>${esc(c.label)}</span></th>`).join('')}</tr></thead>`;
  const shown = () => g.rows.filter((r) => (!q || r.label.toLowerCase().includes(q) || (r.extra || []).some((x) => String(x.value).toLowerCase().includes(q)))
    && (!st || r.worst === st) && (!who || (r.extra || []).some((x) => x.value === who)));
  const body = () => shown().map((r) => `<tr data-row="${esc(r.id)}"><td class="co"><a href="#" class="co-name" data-link title="${esc(r.label)}">${esc(r.label)}</a>${r.sub ? `<div class="small muted">${esc(r.sub)}</div>` : ''}</td>${(r.extra || []).map((x) => `<td class="co-x">${x.value ? `<a href="#" class="dw-who ${x.person ? 'linked' : ''}" data-who="${esc(x.value)}" title="${x.person ? 'Has an account here' : 'A name written down'} — click to show only theirs"><b>${esc(x.value)}</b>${x.person ? '<i>account</i>' : ''}</a>` : '<span class="muted">—</span>'}</td>`).join('')}${g.columns.map((c) => { const cell = (r.cells || {})[c.id] || { dot: 'open', title: '' }; return `<td class="cell"><button type="button" class="dw-dot ${esc(cell.dot)}" data-cell="${esc(c.id)}" title="${esc(cell.title || S(DOT_TITLE[cell.dot] || ''))}"></button></td>`; }).join('')}</tr>`).join('');
  const draw = () => {
    const rows = body();
    el.innerHTML = `<div class="card dw-grid-card"><div class="hd"><h2>${esc(S(w.title || 'page.grid.title'))}</h2><div class="dw-legend">${legend}</div></div>
      ${own && g.rows.length > 3 ? `<div class="bd row eng-grid-bar" style="gap:8px;flex-wrap:wrap;padding-top:0"><input type="search" class="eng-q eng-gq" placeholder="${esc(U('search'))}…" value="${esc(q)}" style="max-width:240px">
        <select class="eng-gst" style="width:auto;max-width:220px"><option value="">${esc(S('col.status') !== 'col.status' ? S('col.status') : 'Status')}: ${esc(U('all'))}</option>${[['expired', 'legend.expired'], ['expiring', 'legend.expiring'], ['missing', 'legend.outstanding'], ['ok', 'legend.in_date']].map(([v, l]) => `<option value="${v}" ${st === v ? 'selected' : ''}>${esc(S(l))}</option>`).join('')}</select>
        ${who ? `<button class="btn sm ghost eng-gwho">${esc(who)} ✕</button>` : ''}<span class="small muted">${shown().length} / ${g.rows.length}</span></div>` : ''}
      ${g.note ? `<div class="bd err-box">${esc(g.note)}</div>` : rows ? `<div class="tbl dw-matrix dw-single"><table>${head}<tbody>${rows}</tbody></table></div>` : `<div class="bd empty">${esc(U('no_rows'))}</div>`}</div>`;
    const gq = el.querySelector('.eng-gq'); if (gq) { let tm; gq.oninput = () => { clearTimeout(tm); tm = setTimeout(() => { q = gq.value.trim().toLowerCase(); draw(); el.querySelector('.eng-gq').focus(); }, 200); }; }
    const gs = el.querySelector('.eng-gst'); if (gs) gs.onchange = () => { st = gs.value; draw(); };
    const gw = el.querySelector('.eng-gwho'); if (gw) gw.onclick = () => { who = ''; draw(); };
  };
  draw();
  el.onclick = async (ev) => {
    const dot = ev.target.closest('button.dw-dot[data-cell]'), name = ev.target.closest('a[data-link]'), wh = ev.target.closest('a[data-who]');
    if (!dot && !name && !wh) return;
    ev.preventDefault();
    if (wh) { who = wh.dataset.who; draw(); return; }
    const tr = ev.target.closest('tr[data-row]'); const r = g.rows.find((x) => String(x.id) === tr.dataset.row);
    if (!r) return;
    if (dot && own) {
      const cell = (r.cells || {})[dot.dataset.cell] || {};
      const info = (env.info || {})[w.id] || {};
      if (cell.row) return openById(env, g.cells.block, cell.row);
      if (!info.block) { toast('Preview: dots open on the live page.'); return; }
      if (!(info.can || {}).add) { toast('Nothing is filed here yet.'); return; }
      return openRowForm(env, info.block, null, null, cell.preset || {});
    }
    if (dot) { const cell = (r.cells || {})[dot.dataset.cell]; env.go((cell && cell.link) || r.link); } else if (r.link) env.go(r.link);
  };
}

async function renderTable(el, w, env, title) {
  const S = env.S, U = lang.ui;
  const info = (env.info || {})[w.id] || {};
  const provider = info.provider || null, block = info.block || null, apiSrc = info.api || (w.source && w.source.api ? { columns: w.source.columns || [] } : null), sheet = info.sheet || null;
  const srcCols = provider ? provider.columns : block ? block.columns : sheet ? sheet.columns : apiSrc ? apiSrc.columns : [];
  const cols = (w.columns && w.columns.length ? w.columns : srcCols) || [];
  const colType = (c) => c.type || (srcCols.find((x) => x.key === c.key) || {}).type || 'text';
  const srcFmt = (c) => c.format || (srcCols.find((x) => x.key === c.key) || {}).format || '';
  const blockCols = block ? block.columns || [] : [];
  const links = block ? await linkChoices(env, block.name, blockCols.filter((c) => (w.filters || []).includes(c.key))) : {};
  // a table of the tool's own: its choice, link and yes/no columns can be filters
  const own = blockCols.filter((c) => (w.filters || []).includes(c.key) && ['choice', 'link', 'bool'].includes(c.type)).map((c) => ({ key: c.key, label: c.label, type: 'choice', own: true,
    options: c.type === 'choice' ? (c.options || []).map((o) => [o, optLabel(S, block.name, c.key, o)]) : c.type === 'bool' ? [['1', U('yes')], ['0', U('no')]] : (links[c.key] || []).map((x) => [String(x.id), x.label]) }));
  const filters = [...(provider ? provider.filters : []).filter((f) => (w.filters || []).includes(f.key)), ...own];
  const can = info.can || {};
  const limit = w.limit || 200;
  const local = !block; // providers and APIs answer whole; search and sort happen here
  let state = { q: '', sort: '', desc: false, params: {}, offset: 0 }, data = { rows: [], total: 0 };
  const fmt = (c, v, r) => {
    const t = colType(c);
    if (v === null || v === undefined || v === '') return '<span class="muted">—</span>';
    if (t === 'date') return esc(lang.date(v));
    if (srcFmt(c) === 'status') return statusPill(String(v));
    if ((t === 'int' || t === 'number' || t === 'calc') && srcFmt(c) === 'money') return `<span class="num">${esc(rand(v))}</span>`;
    if (t === 'int' || t === 'number') return `<span class="num">${esc(lang.num(v, t === 'number' ? 2 : undefined))}</span>`;
    if (t === 'bool') return v ? pill(U('yes'), 'good') : pill(U('no'), 'neutral');
    if (t === 'status') { const dot = r.dot || STATUS_DOT[v] || 'open'; const label = S('status.' + v); return `<span class="row" style="gap:6px;align-items:center"><i class="dw-dot ${esc(dot)}"></i>${esc(label === 'status.' + v ? v : label)}</span>`; }
    if (t === 'json') return `<span class="mono small">${esc(typeof v === 'string' ? v : JSON.stringify(v))}</span>`;
    if (t === 'choice') return pill(optLabel(S, block ? block.name : '', c.key, v), 'neutral');
    if (t === 'link') return esc(((r._labels || {})[c.key]) || `#${v}`);
    if (t === 'person') return esc(((r._labels || {})[c.key]) || '—');
    if (t === 'checklist') return ckSummary(v);
    if (t === 'calc') return typeof v === 'number' ? `<span class="num">${esc(lang.num(v))}</span>` : pill(optLabel(S, block ? block.name : '', c.key, v), 'neutral'); // a grade's word may have its own translation
    if (t === 'file') { const more = v && Array.isArray(v.earlier) && v.earlier.length ? ` <span class="small muted">+${v.earlier.length}</span>` : ''; return v && v.name && block && env.fileUrl ? `<a class="lnk" href="${esc(env.fileUrl(block.name, r.id, c.key))}" download title="${esc(U('download'))}">${esc(v.name)}</a>${more}` : v && v.name ? esc(v.name) + more : more || '<span class="muted">—</span>'; }
    return esc(String(v));
  };
  const rowActs = block ? (w.actions || []).filter((a) => a && (a.flow || a.kind === 'mail')) : [];
  const mine = (r) => can.any_row || r.created_by === (env.me || {}).id || named(r, blockCols, env.me);
  const draw = () => {
    const acts = block && (can.edit || can.delete || rowActs.length);
    const head = cols.map((c) => `<th><button type="button" class="eng-sort ${state.sort === c.key ? 'on' : ''}" data-sort="${esc(c.key)}">${esc(S(c.label || (srcCols.find((x) => x.key === c.key) || {}).label || c.key))}${state.sort === c.key ? (state.desc ? ' ↓' : ' ↑') : ''}</button></th>`).join('') + (acts ? '<th class="act"></th>' : '');
    const rows = data.rows.map((r) => `<tr data-row="${esc(r.id)}" class="${r.link || w.row_link ? 'link' : ''}">${cols.map((c) => `<td>${fmt(c, r[c.key], r)}</td>`).join('')}${acts ? `<td class="act"><div class="row" style="gap:4px;justify-content:flex-end">${rowActs.map((a, i) => `<button class="btn sm" data-act="${i}:${r.id}">${esc(S(a.label))}</button>`).join('')}${can.edit && (mine(r) || block.edit === 'seen') ? `<button class="btn sm ghost" data-edit="${r.id}">${esc(U('edit'))}</button>` : ''}${can.delete && mine(r) ? `<button class="btn sm ghost danger" data-del="${r.id}">${esc(U('delete'))}</button>` : ''}</div></td>` : ''}</tr>`).join('');
    el.innerHTML = `<div class="card"><div class="hd eng-bar">${w.title ? `<h2>${esc(S(w.title))}</h2>` : ''}
        ${w.search !== false ? `<input type="search" class="eng-q" placeholder="${esc(U('search'))}…" value="${esc(state.q)}" style="max-width:260px">` : '<span></span>'}
        <div class="row" style="gap:8px;flex-wrap:wrap">${filters.map((f) => f.own ? `<select data-filter="${esc(f.key)}" style="width:auto"><option value="">${esc(S(f.label || f.key))}: ${esc(U('all'))}</option>${f.options.map(([v, l]) => `<option value="${esc(v)}" ${state.params[f.key] === v ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>` : f.type === 'choice' ? `<select data-filter="${esc(f.key)}" style="width:auto"><option value="">${esc(S(f.label || f.key))}: ${esc(U('all'))}</option>${f.options.map((o) => `<option value="${esc(o)}" ${state.params[f.key] === o ? 'selected' : ''}>${esc(S('status.' + o) !== 'status.' + o ? S('status.' + o) : o)}</option>`).join('')}</select>` : `<input type="number" data-filter="${esc(f.key)}" placeholder="${esc(S(f.label || f.key))}" value="${esc(state.params[f.key] || '')}" style="width:120px">`).join('')}
        ${block && can.add && w.add !== false ? `<button class="btn primary sm eng-add">${esc(U('add'))}</button>` : ''}</div></div>
      ${data.rows.length ? `<div class="tbl"><table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="bd empty">${esc(U('no_rows'))}${data.note ? ` <span class="muted">(${esc(data.note)})</span>` : ''}</div>`}
      <div class="bd small muted eng-foot">${data.rows.length === data.total ? `${lang.num(data.total)} ${esc(U(data.total === 1 ? 'item' : 'items'))}` : `${lang.num(data.rows.length)} ${esc(U('of'))} ${lang.num(data.total)}`}${data.source ? ` · ${esc(data.source)}` : ''}${data.total > data.rows.length && block ? ` · <button class="lnk eng-more">${esc(U('more'))}</button>` : ''}</div></div>`;
    const q = el.querySelector('.eng-q'); if (q) { let t; q.oninput = () => { clearTimeout(t); t = setTimeout(() => { state.q = q.value.trim(); state.offset = 0; load(); }, 250); }; }
    el.querySelectorAll('[data-filter]').forEach((f) => (f.onchange = () => { state.params[f.dataset.filter] = f.value; state.offset = 0; load(); }));
    el.querySelectorAll('[data-sort]').forEach((b) => (b.onclick = () => { if (state.sort === b.dataset.sort) state.desc = !state.desc; else { state.sort = b.dataset.sort; state.desc = false; } if (local) sortLocal(); else load(); }));
    const more = el.querySelector('.eng-more'); if (more) more.onclick = () => { state.offset += limit; load(true); };
    const add = el.querySelector('.eng-add'); if (add) add.onclick = () => rowForm(null);
    el.querySelectorAll('[data-edit]').forEach((b) => (b.onclick = (ev) => { ev.stopPropagation(); rowForm(data.rows.find((r) => String(r.id) === b.dataset.edit)); }));
    el.querySelectorAll('[data-act]').forEach((b) => (b.onclick = async (ev) => {
      ev.stopPropagation();
      const [i, rid] = b.dataset.act.split(':');
      const a = rowActs[+i];
      if (a.kind === 'mail') { await mailDialog(env, w, (w.actions || []).indexOf(a), block.name, +rid); return; }
      await runRowAction(env, a, block.name, +rid, { page: env.page, widget: w.id }, load);
    }));
    el.querySelectorAll('[data-del]').forEach((b) => (b.onclick = async (ev) => { ev.stopPropagation(); if (!(await confirmBox(U('confirm_delete'), U('delete')))) return; try { await env.write.del(block.name, b.dataset.del); toast(U('deleted')); load(); } catch (e) { toast(e.detail || e.message, 'err'); } }));
    el.querySelectorAll('tr.link').forEach((tr) => (tr.onclick = (ev) => { if (ev.target.closest('button,a')) return; const r = data.rows.find((x) => String(x.id) === tr.dataset.row); if (r && r.link) env.go(r.link); else if (r && block && w.row_link) openById(env, block.name, r.id, () => load()); }));
  };
  let all = [];
  const sortLocal = () => { let rows = [...all]; if (state.q) { const n = state.q.toLowerCase(); rows = rows.filter((x) => cols.some((c) => String(x[c.key] ?? '').toLowerCase().includes(n))); } if (state.sort) { const k = state.sort; rows.sort((a, b) => { const x = a[k], y = b[k]; if (x === y) return 0; if (x === null || x === undefined) return 1; if (y === null || y === undefined) return -1; return (x > y ? 1 : -1) * (state.desc ? -1 : 1); }); } data = { ...data, rows, total: rows.length }; draw(); };
  const load = async (append = false) => {
    const qs = new URLSearchParams({ q: local ? '' : state.q, sort: local ? '' : state.sort, desc: state.desc ? '1' : '0', limit: String(limit), offset: String(state.offset), ...Object.fromEntries(Object.entries(state.params).filter(([, v]) => v !== '')) });
    try {
      const r = await env.rows(w, qs.toString());
      if (local) { all = r.rows || []; data = { rows: all, total: all.length, note: r.note, source: r.source }; sortLocal(); }
      else { data = append ? { rows: [...data.rows, ...(r.rows || [])], total: r.total } : { rows: r.rows || [], total: r.total || 0 }; draw(); }
    } catch (e) { el.innerHTML = `<div class="card"><div class="bd err-box">${esc(e.detail || e.message)}</div></div>`; }
  };
  // a new row on a page narrowed to one row of a linked table (a company's page) starts linked to it
  const fromPage = () => Object.fromEntries(Object.entries(env.params || {}).filter(([k]) => blockCols.some((c) => c.key === k && c.type === 'link')).map(([k, v]) => [k, +v]));
  const rowForm = (row) => openRowForm(env, block, row, () => load(), row ? {} : { ...fromPage(), ...(w.preset || {}) });
  el.innerHTML = `<div class="loading"><span class="spin"></span></div>`;
  await load();
}

// A board (1.0.54): one column per option of a pick-list column; a card per row; drag a card to move it
async function renderBoard(el, w, env, title) {
  const S = env.S, U = lang.ui;
  const info = (env.info || {})[w.id] || {};
  const block = info.block;
  if (!block) { el.innerHTML = `<div class="card"><div class="bd err-box">A board shows one of the tool's own tables.</div></div>`; return; }
  const lane = (block.columns || []).find((c) => c.key === w.lane) || { options: [] };
  const titleCol = w.card || ((block.columns || []).find((c) => ['text', 'choice'].includes(c.type || 'text') && c.key !== w.lane) || {}).key;
  const extra = (w.fields || []).map((f) => (typeof f === 'string' ? f : f.key)).filter((k) => k !== titleCol && k !== w.lane).slice(0, 4);
  const can = info.can || {};
  const lanes = [...(lane.options || [])];
  let rows = [];
  const cardHtml = (r) => `<div class="eng-card" draggable="${can.edit ? 'true' : 'false'}" data-card="${esc(r.id)}"><b>${esc(String(r[titleCol] ?? '#' + r.id))}</b>${extra.map((k) => { const c = (block.columns || []).find((x) => x.key === k) || { key: k }; const v = r[k]; if (v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length)) return ''; return `<div class="small muted">${c.type === 'person' || c.type === 'link' ? esc((r._labels || {})[k] || '') : c.type === 'checklist' ? ckSummary(v) : c.type === 'date' ? esc(lang.date(v)) : ['choice', 'calc'].includes(c.type) && typeof v === 'string' ? esc(optLabel(S, block.name, k, v)) : esc(String(v))}</div>`; }).join('')}</div>`;
  const draw = () => {
    const by = new Map(lanes.map((o) => [o, []]));
    for (const r of rows) (by.get(r[w.lane]) || (by.has('') ? by.get('') : by.get(lanes[0]) || [])).push(r);
    const addBtn = can.add && w.add !== false ? `<button class="btn primary sm eng-add">${esc(U('add'))}</button>` : '';
    // 1.0.83: Add sits in the board's own title bar, not alone in an empty strip under it
    const head = title ? title.replace('<span class="eng-w-extra"></span>', `<span class="eng-w-extra">${addBtn}</span>`) : (addBtn ? `<div class="hd eng-bar"><span></span>${addBtn}</div>` : '');
    el.innerHTML = `<div class="card">${head}<div class="eng-board">${lanes.map((o) => `<div class="eng-lane" data-lane="${esc(o)}"><div class="eng-lane-h">${esc(optLabel(S, block.name, w.lane, o))}<span class="muted">${(by.get(o) || []).length}</span></div>${(by.get(o) || []).map(cardHtml).join('')}</div>`).join('')}</div></div>`;
    const add = el.querySelector('.eng-add'); if (add) add.onclick = () => openRowForm(env, block, null, load, { [w.lane]: lanes[0], ...(w.preset || {}) });
    el.querySelectorAll('[data-card]').forEach((c) => {
      c.onclick = () => { const r = rows.find((x) => String(x.id) === c.dataset.card); if (r && can.edit && env.write) openRowForm(env, block, r, load); };
      c.ondragstart = (e) => { e.dataTransfer.setData('text/plain', c.dataset.card); c.classList.add('drag'); };
      c.ondragend = () => c.classList.remove('drag');
    });
    el.querySelectorAll('[data-lane]').forEach((l) => {
      l.ondragover = (e) => { e.preventDefault(); l.classList.add('over'); };
      l.ondragleave = () => l.classList.remove('over');
      l.ondrop = async (e) => {
        e.preventDefault(); l.classList.remove('over');
        const id = e.dataTransfer.getData('text/plain'); const r = rows.find((x) => String(x.id) === id);
        if (!r || r[w.lane] === l.dataset.lane) return;
        if (!env.write) { toast('Preview: cards move on the live page.'); return; }
        const was = r[w.lane]; r[w.lane] = l.dataset.lane; draw();
        try { watchSaved(env, [await env.write.patch(block.name, r.id, { [w.lane]: l.dataset.lane })]); } catch (err) { r[w.lane] = was; draw(); toast(err.detail || err.message, 'err'); }
      };
    });
  };
  const load = async () => { try { const r = await env.rows(w, 'limit=500'); rows = r.rows || []; draw(); } catch (e) { el.innerHTML = `<div class="card"><div class="bd err-box">${esc(e.detail || e.message)}</div></div>`; } };
  el.innerHTML = `<div class="card">${title}<div class="bd"><span class="spin"></span></div></div>`;
  await load();
}

// Totals (1.0.53): a table shown as its numbers — how many rows, and the sum of the columns it adds up
async function renderTotals(el, w, env, title) {
  const S = env.S, U = lang.ui;
  el.innerHTML = `<div class="card">${title}<div class="bd"><span class="spin"></span></div></div>`;
  try {
    const r = await env.rows(w, '');
    const sm = r.summary || { count: (r.rows || []).length, sums: {} };
    const cols = r.columns || [];
    const lab = (k) => { const c = cols.find((x) => x.key === k) || {}; return S(c.label) || c.label || k; };
    const tiles = [`<div class="card dash-tile"><b class="num">${esc(lang.num(sm.count))}</b><span>${esc(U(sm.count === 1 ? 'item' : 'items'))}</span></div>`, ...Object.entries(sm.sums || {}).map(([k, v]) => `<div class="card dash-tile"><b class="num">${esc((cols.find((x) => x.key === k) || {}).format === 'money' ? rand(v) : lang.num(v, 2))}</b><span>${esc(lab(k))}</span></div>`)];
    el.innerHTML = `${w.title ? `<h2 class="eng-sub">${esc(S(w.title))}</h2>` : ''}<div class="dash-tiles">${tiles.join('')}</div>${r.note ? `<p class="small muted">${esc(r.note)}</p>` : ''}`;
  } catch (e) { el.innerHTML = `<div class="card"><div class="bd err-box">${esc(e.detail || e.message)}</div></div>`; }
}

// A chart (1.0.53): one bar per value of a column; its height is how many rows, or the total of a number column
async function renderChart(el, w, env, title) {
  const S = env.S;
  el.innerHTML = `<div class="card">${title}<div class="bd"><span class="spin"></span></div></div>`;
  try {
    const r = await env.rows(w, 'limit=2000');
    const v = w.value || { fn: 'count' };
    const cols = r.columns || [];
    const gcol = cols.find((c) => c.key === w.group) || {};
    const groups = new Map();
    for (const row of r.rows || []) {
      let g = row[w.group]; if (row._labels && row._labels[w.group]) g = row._labels[w.group];
      g = g === null || g === undefined || g === '' ? '—' : String(g);
      const add = v.fn === 'sum' ? Number(String(row[v.of] ?? 0).replace(/[ ,]/g, '')) || 0 : 1;
      groups.set(g, (groups.get(g) || 0) + add);
    }
    const bars = [...groups.entries()].sort((a, b) => b[1] - a[1]).slice(0, 24);
    if (!w.group) { el.innerHTML = `<div class="card">${title}<div class="bd empty">Choose the column the bars are for.</div></div>`; return; }
    if (!bars.length) { el.innerHTML = `<div class="card">${title}<div class="bd empty">${esc(lang.ui('no_rows'))}</div></div>`; return; }
    const max = Math.max(...bars.map((b) => b[1])) || 1;
    const ok = (k) => (gcol.type === 'choice' ? S(`opt.${(w.source || {}).data}.${w.group}.${String(k).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')}`) : '') ;
    el.innerHTML = `<div class="card">${title}<div class="bd eng-chart">${bars.map(([k, n]) => { const l = ok(k); return `<div class="eng-bar"><span class="eng-bar-l" title="${esc(k)}">${esc(l && !l.startsWith('opt.') ? l : k)}</span><span class="eng-bar-t"><i style="width:${Math.max(1, (100 * n) / max).toFixed(1)}%"></i></span><span class="eng-bar-n num">${esc(lang.num(n, v.fn === 'sum' ? 2 : undefined))}</span></div>`; }).join('')}</div></div>`;
  } catch (e) { el.innerHTML = `<div class="card"><div class="bd err-box">${esc(e.detail || e.message)}</div></div>`; }
}

async function renderForm(el, w, env, title) {
  const S = env.S, U = lang.ui;
  const info = (env.info || {})[w.id] || {};
  const block = info.block;
  if (!block) { el.innerHTML = `<div class="card"><div class="bd err-box">This form has no data block.</div></div>`; return; }
  const cols = ((w.fields && w.fields.length ? w.fields.map((f) => ({ ...(block.columns.find((c) => c.key === f.key) || {}), ...f })) : block.columns) || []).filter((c) => c.type !== 'calc');
  const ch = await linkChoices(env, block.name, cols);
  const start = startValues(env, cols, w.preset);
  el.innerHTML = `<div class="card">${title}<div class="bd"><form class="eng-form">${cols.map((c) => field(c, start[c.key], S, U, { block: block.name, links: ch })).join('')}<div class="row" style="justify-content:flex-end;margin-top:6px"><button class="btn primary" type="submit">${esc(w.submit ? S(w.submit) : U('save'))}</button></div></form></div></div>`;
  wireChecklists(el);
  el.querySelector('form').onsubmit = async (ev) => {
    ev.preventDefault();
    if (!env.write) { toast('Preview only', 'err'); return; }
    const values = readForm(ev.target, cols);
    try { const row = await env.write.post(block.name, values); const up = await saveFiles(env, block.name, row.id, ev.target, cols); toast(U('saved')); if (env.refresh) env.refresh(); else ev.target.reset(); watchSaved(env, [row, ...up]); } catch (e) { toast(e.detail || e.message, 'err'); }
  };
}

function field(c, value, S, U, x = {}) {
  if (c.hidden) return '';  // 1.0.88: kept on the row for the server (e.g. Site Schedules' "everything read"), never typed in
  const id = `f-${c.key}-${Math.random().toString(36).slice(2, 6)}`, req = c.required ? ' required' : '';
  const label = `<label for="${id}">${esc(S(c.label || c.key))}${c.required ? ` <span class="muted small">(${esc(U('required'))})</span>` : ''}</label>`;
  const v = value === undefined || value === null ? '' : value;
  if (c.type === 'choice') return `<div class="field">${label}<select id="${id}" name="${esc(c.key)}"${req}><option value="">—</option>${(c.options || []).map((o) => `<option value="${esc(o)}" ${String(v) === String(o) ? 'selected' : ''}>${esc(optLabel(S, x.block || '', c.key, o))}</option>`).join('')}</select></div>`;
  if (c.type === 'link') {
    const list = (x.links || {})[c.key];
    if (!list) return `<div class="field">${label}<select id="${id}" name="${esc(c.key)}" disabled><option>${esc(S(c.table || ''))}…</option></select></div>`;
    return `<div class="field">${label}<select id="${id}" name="${esc(c.key)}"${req}><option value="">—</option>${list.map((o) => `<option value="${esc(o.id)}" ${String(v) === String(o.id) ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}</select></div>`;
  }
  if (c.type === 'person') {
    const list = (x.links || {})[c.key];
    if (!list) return `<div class="field">${label}<select id="${id}" name="${esc(c.key)}" disabled><option>…</option></select></div>`;
    return `<div class="field">${label}<select id="${id}" name="${esc(c.key)}"${req}><option value="">—</option>${list.map((o) => `<option value="${esc(o.id)}" ${String(v) === String(o.id) ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}</select></div>`;
  }
  if (c.type === 'checklist') {
    const items = ckItems(v);
    return `<div class="field">${label}<div class="eng-cklist" data-checklist="${esc(c.key)}">${items.map((it) => `<div class="eng-ck"><input type="checkbox" ${it.done ? 'checked' : ''}><input type="text" value="${esc(it.text)}"></div>`).join('')}<button type="button" class="lnk small" data-ck-add>+ a step</button></div></div>`;
  }
  if (c.type === 'file') {
    const acc = (c.accept || []).map((a) => '.' + String(a).replace(/^\./, '')).join(',');
    const cur = v && v.name ? `<div class="small" style="margin-bottom:4px">${x.url ? `<a class="lnk" href="${esc(x.url)}" download>${esc(v.name)}</a>` : esc(v.name)} · <label class="row" style="display:inline-flex;gap:4px;align-items:center"><input type="checkbox" name="${esc(c.key)}__remove" style="width:auto"> ${esc(U('remove'))}</label></div>` : '';
    const earlier = v && Array.isArray(v.earlier) && v.earlier.length ? `<div class="small muted" style="margin-bottom:4px">Earlier: ${v.earlier.map((e) => (x.url ? `<a class="lnk" href="${esc(x.url)}?v=${encodeURIComponent(e.id)}" download>${esc(e.name)}</a>` : esc(e.name)) + (e.replaced ? ` <span>(until ${esc(lang.date(e.replaced))})</span>` : '')).join(' · ')}</div>` : '';
    // 1.0.83: the same drop area as everywhere else (no bare "Choose File / No file chosen")
    return `<div class="field">${label}${cur}${earlier}<label class="file-in eng-file"><b data-file-name>${esc(cur ? 'Drop a new file here, or click to choose' : 'Drop the file here, or click to choose')}</b>${esc((c.accept || []).join(', ') || 'pdf, png, jpg, docx, xlsx …')} · ${esc(String(c.max_mb || 10))} MB<input type="file" id="${id}" name="${esc(c.key)}"${acc ? ` accept="${esc(acc)}"` : ''}${c.required && !cur ? ' required' : ''}></label></div>`;
  }
  if (c.type === 'bool') return `<div class="field"><label class="row" style="gap:8px;align-items:center;cursor:pointer"><input type="checkbox" id="${id}" name="${esc(c.key)}" ${v ? 'checked' : ''} style="width:auto"> ${esc(S(c.label || c.key))}</label></div>`;
  if (c.options && c.options.length) return `<div class="field">${label}<select id="${id}" name="${esc(c.key)}"${req}><option value="">—</option>${c.options.map((o) => `<option value="${esc(o)}" ${String(v) === String(o) ? 'selected' : ''}>${esc(x && x.block && optLabel(S, x.block, c.key, o) !== String(o) ? optLabel(S, x.block, c.key, o) : (S(o) === o ? o : S(o)))}</option>`).join('')}</select></div>`;
  if (c.type === 'json' || c.multiline) return `<div class="field">${label}<textarea id="${id}" name="${esc(c.key)}"${req}>${esc(typeof v === 'string' ? v : JSON.stringify(v, null, 1))}</textarea></div>`;
  const type = c.type === 'date' ? 'date' : (c.type === 'int' || c.type === 'number') ? 'number' : 'text';
  return `<div class="field">${label}<input type="${type}" id="${id}" name="${esc(c.key)}" value="${esc(String(v).slice(0, 10000))}"${c.type === 'number' ? ' step="any"' : ''}${c.hint ? ` placeholder="${esc(S(c.hint))}"` : ''}${req}></div>`;
}
// the drop areas of file fields: show the chosen name; a dropped file goes into the field
if (!window.__cbxEngFile) {
  window.__cbxEngFile = true;
  const named = (inp) => { const b = inp.closest('.eng-file').querySelector('[data-file-name]'); if (b) b.textContent = inp.files && inp.files.length ? [...inp.files].map((f) => f.name).join(', ') : 'Drop the file here, or click to choose'; };
  document.addEventListener('change', (e) => { if (e.target.matches && e.target.matches('.eng-file input[type=file]')) named(e.target); });
  document.addEventListener('dragover', (e) => { const z = e.target.closest && e.target.closest('.eng-file'); if (z) { e.preventDefault(); z.classList.add('over'); } });
  document.addEventListener('dragleave', (e) => { const z = e.target.closest && e.target.closest('.eng-file'); if (z) z.classList.remove('over'); });
  document.addEventListener('drop', (e) => { const z = e.target.closest && e.target.closest('.eng-file'); if (!z) return; e.preventDefault(); z.classList.remove('over'); const inp = z.querySelector('input[type=file]'); if (inp && e.dataTransfer && e.dataTransfer.files.length) { inp.files = e.dataTransfer.files; named(inp); } });
}
function readForm(form, cols) {
  const out = {};
  for (const c of cols) {
    if (c.type === 'file' || c.type === 'calc') continue;
    if (c.type === 'checklist') { const box = form.querySelector(`[data-checklist="${c.key}"]`); if (box) out[c.key] = JSON.stringify([...box.querySelectorAll('.eng-ck')].map((r) => ({ text: r.querySelector('input[type=text]').value.trim(), done: r.querySelector('input[type=checkbox]').checked })).filter((i) => i.text)); continue; }
    const f = form.elements[c.key]; if (!f || f.disabled) continue; out[c.key] = c.type === 'bool' ? !!f.checked : f.value;
  }
  return out;
}
