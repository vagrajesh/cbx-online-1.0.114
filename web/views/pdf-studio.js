// PDF Studio: Template → PDFs → Reading → Results. Users see a calm version; admins see the workings.
import { api } from '../api.js';
import { esc, toast, modal, pill, fmtDate, fmtNum, fmtMoney, fileDrop, el, confirmBox, statusPill } from '../ui.js';
import { pdfSegHtml, pdfSegWire } from './pdf-seg.js';

const TYPE_LABEL = { text: 'text', number: 'number', money: 'R', date: 'date', list: 'list', filename: 'file', formula: 'calc' };
const TYPE_NAME = { ...TYPE_LABEL, filename: 'file name', formula: 'calculated' };
const TYPE_HELP = {
  text: 'Words as printed (account numbers, names).', number: 'A quantity: kWh, kVA, hours, kms.', money: 'Rands. Checked the hardest: VAT and totals must add up.',
  date: 'A date or a billing period.', list: 'One of a few values (municipality, vendor).',
  filename: "The PDF's own file name. Never read from the page, never a miss.", formula: 'Worked out from other columns, e.g. material_excl_vat * 1.10. Blank while an input is blank.',
};
const NUMERIC_KINDS = ['money', 'number', 'formula'];
const POLL_MS = 2000;
const slugKey = (label) => label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') || 'col';
const isRead = (c) => c.ctype !== 'filename' && c.ctype !== 'formula';
// "22 columns · 3 optional · 2 calculated" — the same words the server uses
function kindsText(cols) {
  const opt = cols.filter((c) => isRead(c) && c.required === false).length, calc = cols.filter((c) => c.ctype === 'formula').length, fn = cols.filter((c) => c.ctype === 'filename').length;
  return `${cols.length} column${cols.length === 1 ? '' : 's'}${opt ? ` · ${opt} optional` : ''}${calc ? ` · ${calc} calculated` : ''}${fn ? ` · ${fn} file name` : ''}`;
}
// quick client-side check so the person sees the problem before saving (the server checks again)
function formulaProblem(expr, cols, ownKey) {
  const e = String(expr || '').trim();
  if (!e) return 'Type the formula, e.g. material_excl_vat * 1.10';
  if (/[^A-Za-z0-9_.,+\-*/()\s]/.test(e)) return 'Only column keys, numbers, + - * / ( ) and round(x, 2) are allowed';
  const usable = cols.filter((c) => NUMERIC_KINDS.includes(c.ctype) && c.key !== ownKey).map((c) => c.key);
  for (const m of e.matchAll(/[A-Za-z_][A-Za-z0-9_]*/g)) {
    const w = m[0].toLowerCase();
    if (['round', 'abs', 'min', 'max'].includes(w)) continue;
    if (w === ownKey) return "A formula can't use its own column";
    if (!usable.includes(w)) return `'${w}' isn't a money, number or calculated column${usable.length ? ` — use ${usable.join(', ')}` : ''}`;
  }
  return '';
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function fmtIsoDate(s) {
  let m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s); if (m) return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
  m = /^(\d{4})-(\d{2})$/.exec(s); if (m) return `${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
  return s;
}
function fmtDateValue(v) {
  const s = String(v);
  const parts = s.split(' – ');
  if (parts.length === 2) { const a = fmtIsoDate(parts[0]), b = fmtIsoDate(parts[1]); const ya = a.slice(-4), yb = b.slice(-4); return ya === yb ? `${a.slice(0, -5)} – ${b}` : `${a} – ${b}`; }
  return fmtIsoDate(s);
}
function fmtCell(cell, col) {
  const v = cell && cell.value;
  if (v === null || v === undefined || v === '') return '';
  if (col.ctype === 'money') return fmtMoney(v);
  if (col.ctype === 'formula') return fmtNum(v, 2);
  if (col.ctype === 'number') return fmtNum(v, Number.isInteger(Number(v)) ? 0 : 2);
  if (col.ctype === 'date') return fmtDateValue(v);
  return String(v);
}

export default {
  title: 'PDF Studio',
  async render(root, ctx) {
    const admin = ctx.me.role !== 'user';
    const S = {
      step: 1,
      templates: [],
      tpl: null, // selected saved template (object) or null
      name: 'Utilities bill',
      cols: [],
      batch: null,
      docs: [],
      uploaded: [],
      sheets: [],
      fix: null, // {row, col}
      learned: null,
      health: null,
      timer: null,
      lastTemplateId: null,
      creating: null, // the one in-flight "save template + create batch" promise, shared by every caller
      uploading: false,
    };
    const dispose = () => { if (S.timer) clearInterval(S.timer); S.timer = null; };

    // ---- load ------------------------------------------------------------------------------
    try { S.muni = (await api.get('/api/pdfstudio/municipal')).profiles; } catch (e) { S.muni = []; }
    try { S.templates = (await api.get('/api/pdfstudio/templates')).templates; } catch (e) { root.innerHTML = `<div class="head"><div><h1>PDF Studio</h1><p>${esc(e.detail || e.message)}</p></div></div>`; return dispose; }
    try { S.sheets = (await api.get('/api/pdfstudio/master-sheets')).sheets; } catch { S.sheets = []; }
    if (admin) { try { S.health = await api.get('/api/health', { quiet: true }); } catch { S.health = null; } }
    if (ctx.params.batch) {
      try {
        S.batch = await api.get(`/api/pdfstudio/batches/${ctx.params.batch}`);
        S.name = S.batch.template.name;
        S.cols = S.batch.template.columns.map((c) => ({ ...c, on: true }));
        S.tpl = S.templates.find((t) => t.id === S.batch.template_id) || null;
        S.step = ['review', 'done'].includes(S.batch.status) ? 4 : (S.batch.status === 'uploading' ? 2 : 3);
      } catch (e) { toast(e.detail || 'Could not open that batch', 'err'); }
    } else {
      try { S.recent = (await api.get('/api/pdfstudio/batches', { quiet: true })).batches.filter((b) => b.status !== 'uploading' || b.docs); } catch { S.recent = []; }
      const live = S.recent.find((b) => ['learning', 'running'].includes(b.status) && !b.stalled);
      if (live && !ctx.params.new) { ctx.go('pdf-studio', { batch: live.id }); return dispose; }  // pick up where C-EXA is working
      if (S.templates.length) useTemplate(S.templates[0]); else S.cols = defaultColumns();
    }

    function useTemplate(t) { S.tpl = t; S.name = t.name; S.cols = t.columns.map((c) => ({ ...c, on: true })); }
    function defaultColumns() {
      return [
        ['account_no', 'Account number', 'text'], ['municipality', 'Municipality', 'list'], ['period', 'Billing period', 'date'], ['kwh', 'kWh consumed', 'number'],
        ['demand_kva', 'Demand kVA', 'number'], ['nmd_kva', 'NMD kVA', 'number'], ['excl_vat', 'Amount excl. VAT', 'money'], ['vat', 'VAT', 'money'], ['total', 'Total due', 'money'],
        ['site_name', 'Site name', 'text', false], ['due_date', 'Due date', 'date', false],
      ].map(([key, label, ctype, on = true]) => ({ key, label, ctype, required: true, critical: ctype === 'money', hint: '', expr: '', on }));
    }
    // does the saved template differ from what is on screen? 'keys' = columns added/removed/reordered;
    // 'attrs' = same columns, only type / required / formula / hint / label changed (can be saved in place)
    function templateDiff() {
      const on = S.cols.filter((c) => c.on);
      if (!S.tpl) return 'keys';
      if (S.tpl.columns.length !== on.length || !S.tpl.columns.every((c, i) => on[i] && on[i].key === c.key)) return 'keys';
      const same = S.tpl.columns.every((c, i) => on[i].label === c.label && on[i].ctype === c.ctype && (on[i].required !== false) === (c.required !== false) && (on[i].expr || '') === (c.expr || '') && (on[i].hint || '') === (c.hint || ''));
      return same ? '' : 'attrs';
    }
    const canEditTpl = () => !!S.tpl && (S.tpl.owner_id === ctx.me.id || ctx.me.role !== 'user');
    const colBody = (c) => ({ key: c.key, label: c.label, ctype: c.ctype, required: isRead(c) ? c.required !== false : false, critical: isRead(c) ? c.critical : false, hint: c.hint || '', expr: c.ctype === 'formula' ? (c.expr || '') : '', optional_reason: c.optional_reason || null });

    const running = () => S.batch && ['learning', 'running'].includes(S.batch.status) && !S.batch.stalled;
    const stalled = () => S.batch && ['learning', 'running'].includes(S.batch.status) && !!S.batch.stalled;
    const finished = () => S.batch && ['review', 'done'].includes(S.batch.status);

    // ---- polling -----------------------------------------------------------------------------
    async function refresh() {
      if (!S.batch) return;
      try {
        const prev = S.batch;
        S.batch = await api.get(`/api/pdfstudio/batches/${S.batch.id}`, { quiet: true });
        if (!prev || prev.step !== S.batch.step || prev.progress !== S.batch.progress || (prev.log || []).length !== (S.batch.log || []).length) S.lastMove = Date.now();
      } catch { return; }
      if (finished() && S.step === 3) { /* stay on the reading step until the user clicks through */ }
      if (!running() && S.timer) { clearInterval(S.timer); S.timer = null; ctx.refreshCounts && ctx.refreshCounts(); }
      draw();
    }
    function startPolling() { if (S.timer) clearInterval(S.timer); S.timer = setInterval(refresh, POLL_MS); }

    // ---- drawing ---------------------------------------------------------------------------------
    function draw() {
      const b = S.batch;
      const on = S.cols.filter((c) => c.on);
      const nDocs = b ? b.docs : S.uploaded.length;
      const runs = b ? (b.runs_count || 0) : 0;
      const sub3 = !b ? '' : running() ? esc(b.step) + '…' : finished() ? (admin ? `Profile built · ${runs} run${runs === 1 ? '' : 's'}` : `Done · checked ${runs} time${runs === 1 ? '' : 's'}`) : '';
      const sub4 = finished() ? `${b.accuracy_pct != null ? b.accuracy_pct + '%' : ''}${b.cells_for_user ? ` · ${b.cells_for_user} cell${b.cells_for_user === 1 ? '' : 's'} for you` : ' · nothing for you'}` : '';
      const offline = admin && S.health && S.health.model && S.health.model.online === false;
      root.innerHTML = `
      ${pdfSegHtml(ctx, 'pdf-studio')}
      <div class="head"><div><h1>PDF Studio</h1><p>Drop in a template and your PDFs. You get a filled sheet back.</p></div>
        <div class="row">${running() ? pill('C-EXA · working', 'ai', true) : ''}${admin ? '<button class="btn sm" id="go-profiles">Profiles &amp; models</button>' : ''}${b ? '<button class="btn sm" id="new-batch">New batch</button>' : ''}</div></div>
      ${offline ? `<div class="thinking" style="margin-bottom:14px"><div class="eyebrow">Ollama offline</div>Running on rules only — profiles are built from heuristics until the model is back.</div>` : ''}
      <div class="stepper">
        ${stepBtn(1, 'Template', `${esc(S.name)} · ${kindsText(on)}`)}
        ${stepBtn(2, 'PDFs', nDocs ? `${nDocs} uploaded` : 'none yet')}
        ${stepBtn(3, 'Reading', sub3)}
        ${stepBtn(4, 'Results', sub4)}
      </div>
      <div id="pane"></div>`;
      pdfSegWire(root, ctx, 'pdf-studio', dispose);
      root.querySelectorAll('.stepper button').forEach((btn) => (btn.onclick = () => { const n = Number(btn.dataset.step); if (!canGo(n)) return; if (n === 2 && !S.batch) { nextToPdfs(); return; } S.step = n; draw(); }));
      const gp = root.querySelector('#go-profiles'); if (gp) gp.onclick = () => ctx.go('pdf-profiles');
      const nb = root.querySelector('#new-batch'); if (nb) nb.onclick = () => { dispose(); S.batch = null; S.uploaded = []; S.fix = null; S.learned = null; S.step = 1; ctx.go('pdf-studio', { new: 1 }); };
      const pane = root.querySelector('#pane');
      if (S.step === 1) drawTemplate(pane);
      else if (S.step === 2) drawPdfs(pane);
      else if (S.step === 3) drawReading(pane);
      else drawResults(pane);
    }
    function stepBtn(n, label, sub) {
      const done = S.step > n || (n === 3 && finished()) || (n === 2 && !!S.batch && S.batch.status !== 'uploading');
      return `<button class="${done ? 'done' : ''}" aria-selected="${S.step === n}" data-step="${n}"><span class="k">${n}</span><span class="l">${label}<span>${sub || ''}</span></span></button>`;
    }
    function canGo(n) {
      if (n === 1) return !S.batch || S.batch.status === 'uploading';
      if (n === 2) return !!S.batch || S.cols.some((c) => c.on);
      if (n === 3) return !!S.batch && S.batch.status !== 'uploading';
      if (n === 4) return finished();
      return false;
    }

    // ---- step 1: template --------------------------------------------------------------------------
    function recentCard() {
      const rs = (S.recent || []).slice(0, 6);
      if (!rs.length || S.batch) return '';
      const row = (b) => `<li><div class="fico xls">${b.status === 'review' ? '!' : 'XLS'}</div><div class="t"><b>${esc(b.name || 'PDF batch')}</b><span>${b.docs} PDF${b.docs === 1 ? '' : 's'} · ${['learning', 'running'].includes(b.status) ? (b.stalled ? 'Stopped — open to start again' : esc(b.step || 'working') + ' · ' + Math.round((b.progress || 0) * 100) + '%') : b.status === 'review' ? `${b.cells_for_user} cell${b.cells_for_user === 1 ? '' : 's'} need you` : b.status === 'done' ? 'Ready' : b.status === 'failed' ? 'Failed' : 'Not started'} · ${fmtDate(b.finished_at || b.created_at, { rel: true })}</span></div>${statusPill(b.status)}<button class="btn sm ${['review', 'learning', 'running'].includes(b.status) ? 'primary' : ''}" data-open="${b.id}">Open</button></li>`;
      return `<div class="card" style="margin-bottom:16px"><div class="hd"><h2>Your batches</h2><span class="small muted">Open one to watch it or fill in what C-EXA couldn't read</span></div><ul class="list">${rs.map(row).join('')}</ul></div>`;
    }
    function drawTemplate(pane) {
      const on = S.cols.filter((c) => c.on);
      const diff = templateDiff();
      // the type slot doubles as the kind tag: "= expr" for a formula, "number · optional" for an optional column
      const colTy = (c) => {
        if (c.ctype === 'formula') return `<span class="ty is-calc" title="${esc(c.expr || '')}">= ${esc(c.expr || '…')}</span>`;
        if (c.ctype !== 'filename' && c.required === false) return `<span class="ty is-opt" title="${esc(TYPE_NAME[c.ctype] || c.ctype)} · ${esc(c.optional_reason || 'blank when the document has no such figure — not a miss')}">optional</span>`;
        return `<span class="ty">${TYPE_LABEL[c.ctype] || c.ctype}</span>`;
      };
      const compact = (t) => { const k = t.columns_summary || {}; return `${t.columns.length} cols${k.optional ? ` · ${k.optional} optional` : ''}${k.calculated ? ` · ${k.calculated} calc` : ''}`; };
      pane.innerHTML = recentCard() + `
      <div class="grid g12">
        <div style="display:flex;flex-direction:column;gap:16px">
          <div id="xl-drop"></div>
          ${S.muni && S.muni.length ? `<div class="card"><div class="bd row between" style="gap:10px;align-items:center"><div><b>Municipal bills</b><div class="small muted">The CBX Harvester knows ${S.muni.length} municipalities (${esc(S.muni.slice(0, 6).map((m) => m.short || m.name).join(', '))}${S.muni.length > 6 ? ' …' : ''}) — their bills are read by its rules, nothing to learn.</div></div><button class="btn sm" id="use-muni">Use</button></div></div>` : ''}
          ${S.templates.length ? `<div class="card"><div class="hd"><h2>Or use a saved template</h2><span class="small muted">shared with your team</span></div>
            <ul class="list">${S.templates.slice(0, 8).map((t) => `<li><div class="t"><b>${esc(t.name)}</b><span>${esc(compact(t))}${t.batches ? ` · ${t.batches} batch${t.batches === 1 ? '' : 'es'}` : ''}${t.accuracy != null ? ` · ${(t.accuracy * 100).toFixed(1)}%` : ''}</span></div><div class="row" style="gap:4px">${S.tpl && S.tpl.id === t.id ? pill('Selected', 'good') : `<button class="btn sm" data-use="${t.id}">Use</button>`}${(t.owner_id === ctx.me.id || ctx.me.role !== 'user') ? `<button class="btn sm ghost" data-rename="${t.id}" title="Rename">Rename</button><button class="btn sm ghost" data-del="${t.id}" title="Delete" style="color:var(--muted)">×</button>` : ''}</div></li>`).join('')}</ul></div>` : ''}
        </div>
        <div class="card"><div class="hd"><h2>Columns</h2><span class="small muted">Tick the ones C-EXA must fill · click the pencil to change a column · PDFs become rows</span></div>
          <div class="bd">
            <div class="field" style="margin-bottom:12px"><label>Template name</label><input type="text" id="tpl-name" value="${esc(S.name)}"></div>
            <div class="cols">
              ${S.cols.map((c, i) => `<div class="colbox ${c.on ? '' : 'off'}" data-i="${i}" style="cursor:pointer" title="${esc(c.hint || TYPE_HELP[c.ctype] || '')}"><span class="check ${c.on ? 'on' : ''}"></span><span class="lbl">${esc(c.label)}</span>${colTy(c)}<button class="cedit" data-edit="${i}" title="Change this column" aria-label="Change ${esc(c.label)}">✎</button></div>`).join('')}
              <div class="colbox" id="add-col" style="border-style:dashed;color:var(--muted);cursor:pointer">+ Add a column</div>
            </div>
            <p class="small muted" style="margin-top:10px">${esc(kindsText(on))}. Money columns (R) are checked the hardest: VAT must be 15% of the amount and the total must add up, or the cell comes to you. <b>Optional</b> columns stay blank when a document has no such figure — that is not a miss. <b>Calculated</b> columns are worked out from other columns.</p>
            <div class="row" style="margin-top:14px;justify-content:flex-end;gap:8px">${diff === 'attrs' && canEditTpl() ? '<button class="btn sm" id="save-tpl">Save template changes</button>' : ''}<button class="btn primary sm" id="next-pdfs" ${on.length && !S.creating ? '' : 'disabled'}>${S.creating ? 'Saving…' : 'Next: add PDFs'}</button></div>
          </div></div>
      </div>`;
      pane.querySelector('#xl-drop').append(fileDrop({ label: 'Drop an Excel template', hint: 'Header row = columns. C-EXA reads it: "file" becomes the file name, travel / km / notes columns start optional.', multiple: false, accept: '.xlsx,.xlsm', onFiles: async (files) => {
        if (!files.length) return;
        try {
          const t = await api.upload('/api/pdfstudio/templates/from-excel', files);
          S.templates.unshift(t); useTemplate(t); draw();
          askName(t, `I read ${t.columns.length} columns (${(t.columns_summary && t.columns_summary.text) || ''}). What should this template be called?`);
        } catch (e) { toast(e.detail || 'Could not read that file', 'err'); }
      } }));
      const um = pane.querySelector('#use-muni');
      if (um) um.onclick = async () => { um.disabled = true; try { const t = await api.post('/api/pdfstudio/templates/municipal', {}); if (!S.templates.some((x) => x.id === t.id)) S.templates.unshift(t); useTemplate(t); draw(); } catch (e) { um.disabled = false; toast(e.detail || 'Could not', 'err'); } };
      pane.querySelectorAll('[data-use]').forEach((b) => (b.onclick = () => { useTemplate(S.templates.find((t) => t.id === Number(b.dataset.use))); draw(); }));
      pane.querySelectorAll('[data-open]').forEach((b) => (b.onclick = () => ctx.go('pdf-studio', { batch: b.dataset.open })));
      pane.querySelectorAll('[data-rename]').forEach((b) => (b.onclick = () => askName(S.templates.find((t) => t.id === Number(b.dataset.rename)), 'Rename template')));
      pane.querySelectorAll('[data-del]').forEach((b) => (b.onclick = async () => {
        const t = S.templates.find((x) => x.id === Number(b.dataset.del));
        if (!(await confirmBox(`Delete the template “${t.name}”?`, 'Delete'))) return;
        try { await api.del(`/api/pdfstudio/templates/${t.id}`); S.templates = S.templates.filter((x) => x.id !== t.id); if (S.tpl && S.tpl.id === t.id) { S.tpl = null; S.name = ''; S.cols = []; } toast('Template deleted'); draw(); } catch (e) { toast(e.detail, 'err'); }
      }));
      pane.querySelectorAll('.colbox[data-i]').forEach((box) => (box.onclick = (e) => { if (e.target.closest('[data-edit]')) return; const c = S.cols[Number(box.dataset.i)]; c.on = !c.on; draw(); }));
      pane.querySelectorAll('[data-edit]').forEach((b) => (b.onclick = (e) => { e.stopPropagation(); columnModal(Number(b.dataset.edit)); }));
      pane.querySelector('#tpl-name').oninput = (e) => { S.name = e.target.value; };
      pane.querySelector('#add-col').onclick = () => columnModal(null);
      pane.querySelector('#next-pdfs').onclick = nextToPdfs;
      const st = pane.querySelector('#save-tpl'); if (st) st.onclick = async () => { st.disabled = true; try { await saveTemplate(); toast('Template saved'); draw(); } catch (e) { st.disabled = false; toast(e.detail || 'Could not save the template', 'err'); } };
    }
    function askName(t, title) {
      const body = el(`<div class="field"><label>Template name</label><input type="text" id="n-name" value="${esc(t.name)}" placeholder="e.g. Capitec bills"></div><p class="small muted">Something your team will recognise — the client, the document, or both.</p>`);
      const m = modal({ title, body, footer: '<button class="btn" data-close>Later</button><button class="btn primary" id="n-ok">Save name</button>' });
      const save = async () => {
        const name = body.querySelector('#n-name').value.trim(); if (!name) { toast('Give it a name', 'err'); return; }
        try { const u = await api.patch(`/api/pdfstudio/templates/${t.id}`, { name }); Object.assign(t, u); if (S.tpl && S.tpl.id === t.id) { S.tpl = t; S.name = u.name; } m.close(); toast(`Renamed to “${u.name}”`); draw(); } catch (e) { toast(e.detail, 'err'); }
      };
      m.root.querySelector('#n-ok').onclick = save;
      body.querySelector('#n-name').onkeydown = (e) => { if (e.key === 'Enter') save(); };
      setTimeout(() => body.querySelector('#n-name').select(), 50);
    }
    // Add or change a column: name, type (file name / calculated included), formula with key chips,
    // where it is, and whether it must be on every document.
    function columnModal(index) {
      const cur = index == null ? { label: '', ctype: 'text', hint: '', required: true, expr: '', key: '' } : S.cols[index];
      const others = S.cols.filter((c, i) => i !== index);
      const body = el(`<div style="display:flex;flex-direction:column;gap:12px">
        <div class="field"><label>Column name</label><input type="text" id="c-label" value="${esc(cur.label)}" placeholder="e.g. Meter number"></div>
        <div class="field"><label>Type</label><select id="c-type">${Object.keys(TYPE_LABEL).map((t) => `<option value="${t}" ${t === cur.ctype ? 'selected' : ''}>${t === 'money' ? 'money (R)' : TYPE_NAME[t]}</option>`).join('')}</select><p class="small muted" id="c-help" style="margin-top:4px"></p></div>
        <div class="field" id="c-expr-box" style="display:none"><label>Formula</label><input type="text" id="c-expr" value="${esc(cur.expr || '')}" placeholder="e.g. material_excl_vat * 1.10" spellcheck="false" style="font-family:var(--mono)">
          <div class="chips" id="c-chips">${others.filter((c) => NUMERIC_KINDS.includes(c.ctype)).map((c) => `<button type="button" class="chip" data-key="${esc(c.key)}" title="${esc(c.label)}">${esc(c.key)}</button>`).join('') || '<span class="small muted">Add a money or number column first — a formula needs something to work from.</span>'}</div>
          <p class="small" id="c-expr-msg" style="margin-top:4px;min-height:1em"></p>
          <p class="small muted">Click a key to insert it. Numbers, + − × ÷ ( ) and round(x, 2). Blank on a row while any input is blank.</p></div>
        <div class="field" id="c-hint-box"><label>Where is it on the PDF? (optional)</label><input type="text" id="c-hint" value="${esc(cur.hint || '')}" placeholder='e.g. "Meter no" on page 1, right of the label'></div>
        <label class="row small" id="c-req-box" style="gap:8px"><input type="checkbox" id="c-req" ${cur.required !== false ? 'checked' : ''} style="width:auto"> Required — must be on every document (untick for figures most documents don't have: travel, kms, deposits; a blank cell is then the answer, not a miss)</label>
        ${cur.optional_reason ? `<p class="small muted">Why optional: ${esc(cur.optional_reason)}</p>` : ''}
      </div>`);
      const m = modal({ title: index == null ? 'Add a column' : `Change “${cur.label}”`, body, footer: `<button class="btn" data-close>Cancel</button>${index != null && S.cols.length > 1 ? '<button class="btn ghost" id="c-del" style="margin-right:auto;color:var(--crit)">Remove column</button>' : ''}<button class="btn primary" id="c-ok">${index == null ? 'Add' : 'Save'}</button>` });
      const typeSel = body.querySelector('#c-type'), exprIn = body.querySelector('#c-expr'), msg = body.querySelector('#c-expr-msg');
      const keyOf = () => cur.key || slugKey(body.querySelector('#c-label').value.trim());
      const show = (id, on) => { body.querySelector(id).style.display = on ? '' : 'none'; };
      const sync = () => {
        const t = typeSel.value;
        body.querySelector('#c-help').textContent = TYPE_HELP[t] || '';
        show('#c-expr-box', t === 'formula');
        show('#c-hint-box', isRead({ ctype: t }));
        show('#c-req-box', isRead({ ctype: t }));
        if (t === 'formula') { const p = formulaProblem(exprIn.value, others, keyOf()); msg.textContent = p || (exprIn.value.trim() ? 'Looks right.' : ''); msg.style.color = p ? 'var(--crit)' : 'var(--good)'; }
      };
      typeSel.onchange = sync; exprIn.oninput = sync; sync();
      body.querySelectorAll('.chip').forEach((ch) => (ch.onclick = () => {
        const k = ch.dataset.key, v = exprIn.value, pos = exprIn.selectionStart ?? v.length;
        const before = v.slice(0, pos), after = v.slice(pos);
        const ins = (before && !/[\s(*/+\-,]$/.test(before) ? ' ' : '') + k + (after && !/^[\s)*/+\-,]/.test(after) ? ' ' : '');
        exprIn.value = before + ins + after; exprIn.focus(); exprIn.setSelectionRange(before.length + ins.length, before.length + ins.length); sync();
      }));
      const del = m.root.querySelector('#c-del'); if (del) del.onclick = () => { S.cols.splice(index, 1); m.close(); draw(); };
      m.root.querySelector('#c-ok').onclick = () => {
        const label = body.querySelector('#c-label').value.trim(); if (!label) { toast('Give the column a name', 'err'); return; }
        const ctype = typeSel.value;
        const dup = S.cols.find((c, i) => i !== index && c.label.toLowerCase() === label.toLowerCase());
        if (dup) { if (index == null) { dup.on = true; m.close(); draw(); return; } toast('Another column already has that name', 'err'); return; }
        let expr = '';
        if (ctype === 'formula') { expr = exprIn.value.trim(); const p = formulaProblem(expr, others, keyOf()); if (p) { toast(p, 'err'); return; } }
        const req = isRead({ ctype }) ? body.querySelector('#c-req').checked : false;
        const next = { ...cur, key: cur.key || slugKey(label), label, ctype, required: req, critical: ctype === 'money' && req, hint: isRead({ ctype }) ? body.querySelector('#c-hint').value.trim() : '', expr, on: true };
        if (isRead({ ctype }) && req) delete next.optional_reason;
        if (index == null) S.cols.push(next); else S.cols[index] = next;
        m.close(); draw();
      };
      setTimeout(() => { if (!body.contains(document.activeElement)) body.querySelector(index == null ? '#c-label' : (cur.ctype === 'formula' ? '#c-expr' : '#c-label')).focus(); }, 50);
    }
    // Save the on-screen columns into the selected template (same columns, changed attributes).
    async function saveTemplate() {
      const on = S.cols.filter((c) => c.on);
      const u = await api.patch(`/api/pdfstudio/templates/${S.tpl.id}`, { name: (S.name || '').trim() || S.tpl.name, columns: on.map(colBody) });
      Object.assign(S.tpl, u); const i = S.templates.findIndex((x) => x.id === u.id); if (i >= 0) S.templates[i] = S.tpl;
      S.name = u.name; S.cols = u.columns.map((c) => ({ ...c, on: true }));
      return u;
    }
    // Save the template (new, renamed, or changed in place) and make sure there is exactly ONE batch for
    // it. Every caller — the "Next" button, the stepper, a drop of 1 500 PDFs — awaits the same promise,
    // so concurrent calls can never create a second batch.
    function ensureBatch() {
      if (S.creating) return S.creating;
      S.creating = (async () => {
        const on = S.cols.filter((c) => c.on);
        if (!on.length) throw new Error('Tick at least one column first.');
        const name = (S.name || '').trim() || 'Template';
        let tplId = null;
        const diff = templateDiff();
        if (S.tpl && diff !== 'keys' && (S.tpl.name === name || canEditTpl())) {
          if ((diff === 'attrs' || S.tpl.name !== name) && canEditTpl()) await saveTemplate();
          tplId = S.tpl.id;
        } else {
          const t = await api.post('/api/pdfstudio/templates', { name, columns: on.map(colBody) });
          S.templates.unshift(t); S.tpl = t; S.name = t.name; S.cols = t.columns.map((c) => ({ ...c, on: true }));
          tplId = t.id;
        }
        if (!S.batch || S.batch.template_id !== tplId || S.batch.status !== 'uploading') {
          const b = await api.post('/api/pdfstudio/batches', { template_id: tplId, name: `${name} · ${new Date().toLocaleDateString('en-ZA', { day: 'numeric', month: 'short' })}` });
          S.batch = await api.get(`/api/pdfstudio/batches/${b.id}`);
          S.uploaded = [];
        }
        return S.batch;
      })();
      const p = S.creating;
      p.then(() => { if (S.creating === p) S.creating = null; }, () => { if (S.creating === p) S.creating = null; });
      return p;
    }
    async function nextToPdfs() {
      if (S.creating) return;
      const p = ensureBatch();
      if (S.step === 1) draw(); // shows "Saving…" and disables the button while the one promise runs
      try { await p; S.step = 2; draw(); }
      catch (e) { toast(e.detail || e.message || 'Could not save the template', 'err'); draw(); }
    }
    // A drop that failed before a single PDF arrived leaves nothing behind.
    async function dropEmptyBatch() {
      const b = S.batch; if (!b || b.docs) return;
      try { await api.del(`/api/pdfstudio/batches/${b.id}`, { quiet: true }); } catch { /* housekeeping prunes it within a day */ }
      S.batch = null;
    }

    // ---- step 2: PDFs ---------------------------------------------------------------------------------
    function drawPdfs(pane) {
      const docs = S.batch ? S.batch.documents : [];
      const scanned = docs.filter((d) => d.scanned).length;
      pane.innerHTML = `
      <div class="grid g21">
        <div class="card"><div class="hd"><h2>${docs.length} PDF${docs.length === 1 ? '' : 's'}</h2><span class="small muted">Saved to your folder and the shared library</span></div>
          <div class="bd">
            <div id="pdf-drop" style="margin-bottom:14px"></div>
            ${docs.length ? `<div class="pdfgrid">${docs.map((d) => `<div class="pg" title="${esc(d.filename)}${d.pages ? ` · ${d.pages} page${d.pages === 1 ? '' : 's'}` : ''}"></div>`).join('')}</div>` : '<p class="small muted">Nothing uploaded yet. Drop the PDFs above — 1 or 300, it works the same.</p>'}
            <div class="row" style="margin-top:14px;justify-content:space-between"><span class="small muted">${docs.length ? `${docs.length} uploaded${scanned ? ` · ${scanned} scanned` : ''} · ${docs.reduce((a, d) => a + (d.pages || 0), 0)} pages` : ''}</span><button class="btn primary sm" id="hand" ${docs.length ? '' : 'disabled'}>Hand to C-EXA</button></div>
          </div></div>
        ${admin ? `<div class="card"><div class="hd"><h2>How C-EXA will work</h2></div>
          <ul class="list">
            <li><div class="t"><b>Reads a sample</b><span>First 3 PDFs of each layout, page by page</span></div></li>
            <li><div class="t"><b>Builds a profile</b><span>Where each column lives on this kind of bill</span></div></li>
            <li><div class="t"><b>Scores herself</b><span>Each column = 1/${S.cols.filter((c) => c.on).length || 9} of the mark. Below 90% she goes back</span></div></li>
            <li><div class="t"><b>Runs the rest</b><span>Any PDF under 90% → opens it, fixes the profile, reruns all</span></div></li>
            <li><div class="t"><b>Stops at 99%</b><span>Then fills the sheet and gives you only the gaps</span></div></li>
          </ul></div>` : `<div class="card"><div class="bd"><b>What happens next</b><p class="small muted" style="margin-top:4px">C-EXA reads them, checks her work, and hands you a sheet. If she's unsure about a cell, she asks you — nothing else.</p></div></div>`}
      </div>`;
      pane.querySelector('#pdf-drop').append(fileDrop({ label: 'Drop your PDFs here', hint: 'or click to choose · any number of files', multiple: true, accept: '.pdf,application/pdf', onFiles: async (files) => {
        if (!files.length) return;
        if (S.uploading) { toast('Still saving the last drop — wait for it to finish, then drop the rest.', 'err'); return; }
        S.uploading = true;
        try {
          // ONE batch, created once, before any file is posted — every chunk below reuses it
          if (!S.batch || S.batch.status !== 'uploading') {
            toast('Saving your template first…');
            try { await ensureBatch(); } catch (e) { toast(e.detail || e.message || 'Could not create the batch — check the template has at least one column ticked.', 'err'); return; }
            pane = root.querySelector('#pane') || pane;
          }
          const batchId = S.batch.id;
          // Upload in small groups so the counter moves and one bad file doesn't sink the lot.
          const total = files.length; let done = 0, failed = 0; const CHUNK = 5;
          const drop = pane.querySelector('#pdf-drop');
          const prog = el(`<div class="card" style="margin-bottom:14px"><div class="bd"><div class="row between" style="margin-bottom:8px"><b>Uploading <span id="up-n">0</span> / ${total}</b><span class="small muted" id="up-cur"></span></div><div class="progress"><i id="up-bar" style="width:0%"></i></div><p class="small muted" style="margin-top:8px">Each file is saved to your folder as it arrives. You can keep this page open — nothing is read until you click “Hand to C-EXA”.</p></div></div>`);
          if (drop) drop.replaceWith(prog);
          const sorted = [...files].sort((a, b) => a.name.localeCompare(b.name));
          let stop = false;
          for (let i = 0; i < sorted.length && !stop; i += CHUNK) {
            const part = sorted.slice(i, i + CHUNK);
            prog.querySelector('#up-cur').textContent = part.map((f) => f.name).join(', ').slice(0, 80);
            try {
              const r = await api.upload(`/api/pdfstudio/batches/${batchId}/files`, part);
              done += r.documents.length; failed += (r.rejected || []).length;
              if (r.rejected && r.rejected.length) toast(`Not PDFs, skipped: ${r.rejected.join(', ')}`, 'err');
            } catch (e) {
              failed += part.length;
              // the batch is gone or refuses files (409): no point posting the remaining chunks at it
              if (e.status === 404 || e.status === 409 || e.status === 401 || e.status === 403) { stop = true; failed = total - done; }
              toast(`${part.length} file${part.length === 1 ? '' : 's'} failed: ${e.detail || 'upload error'}`, 'err');
            }
            prog.querySelector('#up-n').textContent = done;
            prog.querySelector('#up-bar').style.width = Math.round(((done + failed) / total) * 100) + '%';
          }
          if (!done && !(S.batch && S.batch.docs)) { await dropEmptyBatch(); toast('Nothing was saved, so no batch was made. Fix the files and drop them again.', 'err'); draw(); return; }
          try { S.batch = await api.get(`/api/pdfstudio/batches/${batchId}`); } catch { /* keep what we have */ }
          toast(failed ? `${done} of ${total} saved · ${failed} failed` : `${done} PDF${done === 1 ? '' : 's'} saved`, failed ? 'err' : ''); draw();
        } finally { S.uploading = false; }
      } }));
      pane.querySelector('#hand').onclick = async () => {
        try {
          await api.post(`/api/pdfstudio/batches/${S.batch.id}/start`);
          S.batch = await api.get(`/api/pdfstudio/batches/${S.batch.id}`);
          S.step = 3; draw();
          if (running()) startPolling();
        } catch (e) { toast(e.detail || 'Could not start', 'err'); }
      };
    }

    // ---- step 3: reading ---------------------------------------------------------------------------------
    function drawReading(pane) {
      const b = S.batch;
      if (!b) { pane.innerHTML = '<div class="empty">Nothing running.</div>'; return; }
      const runs = b.runs_count || 0;
      const pct = b.accuracy_pct != null ? b.accuracy_pct + '%' : '';
      const calm = running()
        ? `<div class="card" style="margin-bottom:16px"><div class="bd"><div class="row between"><div><b>C-EXA is reading your ${b.docs} PDF${b.docs === 1 ? '' : 's'}.</b><div class="small muted">${esc(b.step)}${runs ? ` · checked ${runs} time${runs === 1 ? '' : 's'} so far` : ''}</div></div>${pill(b.step.split(' · ')[0], 'ai', true)}</div><div class="meter" style="margin-top:12px"><i style="width:${Math.round((b.progress || 0) * 100)}%"></i></div><div class="row between small muted" style="margin-top:8px"><span>${Math.round((b.progress || 0) * 100)}% · last movement ${S.lastMove ? Math.max(0, Math.round((Date.now() - S.lastMove) / 1000)) + ' s ago' : 'just now'}</span><span>${S.lastMove && Date.now() - S.lastMove > 120000 ? 'Taking longer than usual on this step — C-EXA is still on it (big or scanned files take longer).' : 'Updates every few seconds.'}</span></div></div></div>`
        : stalled()
          ? `<div class="card" style="margin-bottom:16px"><div class="bd row between"><div><b>C-EXA stopped before finishing.</b><div class="small muted">The server restarted while this batch was running. Nothing is lost — start it again.</div></div><button class="btn primary sm" id="restart">Start again</button></div></div>`
          : b.status === 'failed'
          ? `<div class="card" style="margin-bottom:16px"><div class="bd row between"><div><b>Something went wrong.</b><div class="small muted">${esc(admin ? b.error || '' : (b.error === 'Upload never finished — start again' ? 'The upload never finished. Your PDFs are still here — try again.' : 'C-EXA could not finish this batch. An admin has been told.'))}</div></div><button class="btn primary sm" id="restart">Try again</button></div></div>`
          : `<div class="card" style="margin-bottom:16px"><div class="bd row between"><div><b>All ${b.docs} PDFs read.</b><div class="small muted">C-EXA checked her own work ${runs} time${runs === 1 ? '' : 's'} and got to ${pct}. ${b.cells_for_user ? `${b.cells_for_user} cell${b.cells_for_user === 1 ? '' : 's'} need you.` : 'Nothing needs you.'}${b.reruns ? ` Re-read ${b.reruns} time${b.reruns === 1 ? '' : 's'} with a newer profile.` : ''}</div></div><div class="row">${admin ? '<button class="btn sm" id="rerun">Re-read with current profile</button>' : ''}<button class="btn primary sm" id="see-results">See results</button></div></div></div>`;
      let adminHtml = '';
      if (admin) {
        const rs = b.runs || [];
        const maxH = 86;
        const runsHtml = rs.length ? rs.map((r) => `<div class="r"><b>${(r.accuracy * 100).toFixed(r.accuracy >= 0.99 ? 1 : 0)}%</b><i style="height:${Math.max(6, Math.round(r.accuracy * maxH))}px"></i>Run ${r.n}<br>${esc(r.note)}</div>`).join('') : '<p class="small muted">No runs yet.</p>';
        const cols = b.template.columns;
        const bc = b.by_column || {};
        const scoreHtml = cols.map((c) => { const v = bc[c.key]; const p = v == null ? 0 : Math.round(v * 100); const cls = p >= 99 ? '' : p >= 90 ? 'mid' : 'low'; return `<div class="score"><span title="${esc(c.label)}">${esc(c.label)}</span><div class="bar"><i class="${cls}" style="width:${p}%"></i></div><span class="pct">${v == null ? '—' : p}</span></div>`; }).join('');
        const pg = b.documents.map((d) => `<div class="pg ${d.score == null ? '' : d.score >= 0.9 ? 'ok' : d.score >= 0.5 ? 'warn' : 'bad'} ${d.is_sample ? 'sample' : ''}" title="#${d.ordinal} ${esc(d.filename)}${d.score != null ? ` · ${(d.score * 100).toFixed(0)}%` : ''}${d.scanned ? ' · scanned' : ''}"></div>`).join('');
        const fully = b.documents.filter((d) => d.score != null && d.score >= 0.9).length;
        const scannedLow = b.documents.filter((d) => d.scanned && d.score != null && d.score < 0.9).length;
        const logHtml = (b.log || []).map((e) => `<div><span>${fmtDate(e.at)}</span><div>${esc(e.text).replace(/profile v(\d+)/g, '<em>profile v$1</em>').replace(/(\d+(?:\.\d+)?%)/g, '<em>$1</em>')}</div></div>`).join('') || '<div><span></span><div class="muted">Waiting for C-EXA…</div></div>';
        adminHtml = `
        <div class="grid g3" style="margin-bottom:16px">
          <div class="card"><div class="hd"><h2>Accuracy per run</h2><span class="small muted">target 99%</span></div><div class="bd"><div class="runs">${runsHtml}</div></div></div>
          <div class="card"><div class="hd"><h2>Score by column</h2><span class="small muted">${rs.length ? `run ${rs.length}` : ''}</span></div><div class="bd" style="padding-top:8px">${scoreHtml}</div></div>
          <div class="card"><div class="hd"><h2>PDFs</h2><span class="small muted">S = sample</span></div><div class="bd"><div class="pdfgrid" style="grid-template-columns:repeat(6,1fr)">${pg}</div>
            <p class="small muted" style="margin-top:10px">${fully} of ${b.docs} fully read.${scannedLow ? ` ${scannedLow} scanned bill${scannedLow === 1 ? ' has' : 's have'} cells C-EXA isn't sure about.` : ''}${b.spend_zar ? ` Model spend R ${b.spend_zar.toFixed(2)} of R ${b.ceiling_zar}.` : ' No model calls needed — rules only.'}</p></div></div>
        </div>
        <div class="card"><div class="hd"><h2>C-EXA's log</h2>${pill('what she did and why', 'ai')}</div><div class="cexa-log">${logHtml}</div></div>`;
      }
      pane.innerHTML = calm + adminHtml;
      const sr = pane.querySelector('#see-results'); if (sr) sr.onclick = () => { S.step = 4; draw(); };
      const rb = pane.querySelector('#restart'); if (rb) rb.onclick = async () => {
        rb.disabled = true;
        try { await api.post(`/api/pdfstudio/batches/${b.id}/start`); S.batch = await api.get(`/api/pdfstudio/batches/${b.id}`); S.lastMove = Date.now(); draw(); if (running()) startPolling(); }
        catch (e) { rb.disabled = false; toast(e.detail || 'Could not start', 'err'); }
      };
      const rr = pane.querySelector('#rerun'); if (rr) rr.onclick = () => rerunBatch(b);
      if (running() && !S.timer) startPolling();
    }
    // Admin: read every PDF of this batch again with the template's CURRENT profile.
    async function rerunBatch(b) {
      const ok = await confirmBox(`Re-read all ${b.docs} PDF${b.docs === 1 ? '' : 's'} of “${b.name}” with the current profile? C-EXA reads the stored copies again with today's rules and the columns as they are now (${(b.columns_summary && b.columns_summary.text) || ''}). Cells people filled or trusted stay; every other cell is replaced, and each row's earlier values are kept in its history. The sheet is unavailable while it runs.`, 'Re-read');
      if (!ok) return;
      try {
        await api.post(`/api/pdfstudio/batches/${b.id}/rerun`);
        S.batch = await api.get(`/api/pdfstudio/batches/${b.id}`); S.fix = null; S.lastMove = Date.now(); S.step = 3; draw();
        if (running()) startPolling();
      } catch (e) { toast(e.detail || 'Could not start the re-read', 'err'); }
    }

    // ---- step 4: results --------------------------------------------------------------------------------
    function drawResults(pane) {
      const b = S.batch;
      if (!b || !b.sheet) { pane.innerHTML = '<div class="empty">The sheet isn\'t ready yet.</div>'; return; }
      const cols = b.sheet.columns;
      const rows = b.sheet.rows;
      const total = rows.length * cols.length;
      const filled = rows.reduce((a, r) => a + cols.filter((c) => { const s = (r.cells[c.key] || {}).status; return ['ok', 'filled', 'trusted'].includes(s) && (r.cells[c.key] || {}).value != null; }).length, 0);
      const absent = rows.reduce((a, r) => a + cols.filter((c) => (r.cells[c.key] || {}).status === 'absent').length, 0);
      const forYou = b.cells_for_user || 0;
      const profiles = b.profiles || [];
      const kinds = (b.columns_summary && b.columns_summary.text) || kindsText(cols);
      const sheetOpts = S.sheets.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
      pane.innerHTML = `
      <div class="grid g3" style="margin-bottom:16px">
        <div class="card stat"><div class="eyebrow">Filled</div><div class="v num">${filled} / ${total}</div><div class="d">cells · ${rows.length} row${rows.length === 1 ? '' : 's'} × ${esc(kinds)}${absent ? ` · ${absent} blank (not on the document)` : ''}</div></div>
        <div class="card stat"><div class="eyebrow">For you</div><div class="v num" style="color:${forYou ? 'var(--warn)' : 'var(--good)'}">${forYou}</div><div class="d">${forYou ? "cells C-EXA couldn't read with confidence" : 'nothing left to check'}</div></div>
        ${admin ? `<div class="card stat"><div class="eyebrow">Profile${profiles.length === 1 ? '' : 's'}</div><div class="v">${profiles.map((p) => `v${p.version}`).join(' · ') || '—'}</div><div class="d">${esc(profiles.map((p) => p.label.split(' · ').pop()).join(', ') || b.template.name)} · learns from your fixes</div></div>`
          : `<div class="card stat"><div class="eyebrow">Read</div><div class="v num">${b.docs}</div><div class="d">PDF${b.docs === 1 ? '' : 's'} · ${b.documents.reduce((a, d) => a + (d.pages || 0), 0)} pages${b.documents.some((d) => d.scanned) ? ` · ${b.documents.filter((d) => d.scanned).length} scanned` : ''}</div></div>`}
      </div>
      <div class="card" style="margin-bottom:16px"><div class="hd"><h2>Sheet</h2><div class="row"><a class="btn sm" href="/api/pdfstudio/batches/${b.id}/export.xlsx">Download Excel</a>${admin ? '<button class="btn sm" id="rerun" title="Read every PDF again with the template\'s current profile">Re-read with current profile</button>' : ''}${S.sheets.length ? `<select id="ms-sel" style="width:auto">${sheetOpts}</select><button class="btn sm primary" id="to-master">Add to master sheet</button>` : ''}</div></div>
        <div class="tbl sheet"><table>
          <thead><tr><th>#</th>${cols.map((c) => `<th title="${esc(c.ctype === 'formula' ? `= ${c.expr || ''}` : c.kind || '')}">${esc(c.label)}${c.ctype === 'formula' ? ' <span class="cth">calc</span>' : c.ctype !== 'filename' && c.required === false ? ' <span class="cth">optional</span>' : ''}</th>`).join('')}${admin ? '<th>Correct</th>' : ''}</tr></thead>
          <tbody>${rows.map((r) => `<tr>${`<td class="muted" title="${esc(r.filename)}">${r.ordinal}</td>`}${cols.map((c) => cellTd(r, c)).join('')}${admin ? `<td class="num small muted">${r.correct} of ${r.of}</td>` : ''}</tr>`).join('')}</tbody>
        </table></div>
        <div class="bd small muted" style="border-top:1px solid var(--line)">${rows.length} row${rows.length === 1 ? '' : 's'}. <span style="color:var(--warn);font-weight:600">Orange</span> = C-EXA needs you — click the cell. <span style="color:var(--good);font-weight:600">Green</span> = you filled it and C-EXA confirmed it's on the PDF. <span style="color:var(--crit);font-weight:600">Red</span> = your value isn't on the PDF; answer C-EXA's question.${absent ? ' <b>—</b> = an optional column that is not on that document (blank is the answer; click it if you have the figure).' : ''}${cols.some((c) => c.ctype === 'formula') ? ' <b>calc</b> columns are worked out from other columns.' : ''}</div>
      </div>
      <div id="fix"></div>`;
      const tm = pane.querySelector('#to-master'); if (tm) tm.onclick = async () => {
        const sid = Number(pane.querySelector('#ms-sel').value); const sheet = S.sheets.find((s) => s.id === sid);
        try { const r = await api.post(`/api/pdfstudio/batches/${b.id}/to-master/${sid}`); toast(`Sent to Master Sheets › ${sheet ? sheet.name : ''}: ${r.added} row${r.added === 1 ? '' : 's'} added${r.exceptions ? `, ${r.exceptions} to exceptions` : ''}${r.skipped ? `, ${r.skipped} already there` : ''} · logged`); } catch (e) { toast(e.detail || 'Could not add', 'err'); }
      };
      if (S.fix) drawFix(pane.querySelector('#fix'));
      pane.querySelectorAll('td[data-doc]').forEach((td) => { td.onclick = () => { S.fix = { doc: Number(td.dataset.doc), col: td.dataset.col }; drawFix(pane.querySelector('#fix')); }; });
      const rr = pane.querySelector('#rerun'); if (rr) rr.onclick = () => rerunBatch(b);
    }
    function cellTd(r, c) {
      const cell = r.cells[c.key] || { status: 'miss' };
      const st = cell.status;
      const numeric = c.ctype === 'money' || c.ctype === 'number' || c.ctype === 'formula';
      const clickable = `data-doc="${r.document_id}" data-col="${esc(c.key)}"`;
      if (c.ctype === 'formula' || c.ctype === 'filename') {
        if (cell.value == null) return `<td class="absent ${numeric ? 'num' : ''}" title="${esc(cell.note || 'blank')}">—</td>`;
        return `<td class="${numeric ? 'num' : ''} ${c.ctype === 'filename' ? 'small' : ''}" title="${esc(c.ctype === 'formula' ? `= ${c.expr || ''}` : 'the PDF\'s file name')}">${esc(fmtCell(cell, c))}</td>`;
      }
      if (st === 'absent') return `<td class="absent ${numeric ? 'num' : ''}" ${clickable} title="Not on this document — optional column, so this is not a miss. Click if you have the figure.">—</td>`;
      if (st === 'miss') return `<td class="miss" ${clickable} title="C-EXA: ${esc(cell.note || 'not found')}">${cell.raw && cell.value == null ? '? ' + esc(String(cell.raw).slice(0, 12)) : cell.value != null ? '? ' + esc(fmtCell(cell, c)) : '?'}</td>`;
      if (st === 'flagged') return `<td class="flag" ${clickable} title="${esc(cell.note || '')}">${esc(fmtCell(cell, c))}</td>`;
      if (st === 'filled' || st === 'trusted') return `<td class="filled ${numeric ? 'num' : ''}" ${clickable} title="${esc(cell.note || '')}">${esc(fmtCell(cell, c))}</td>`;
      return `<td class="${numeric ? 'num' : c.key.includes('account') ? 'mono' : ''}" ${admin ? clickable : ''} title="${admin ? `page ${cell.page || '?'} · ${esc(cell.quote || '')} · ${esc(cell.method || '')}` : ''}">${esc(fmtCell(cell, c))}</td>`;
    }
    async function drawFix(box) {
      const b = S.batch;
      if (!S.fix || !box) return;
      const row = b.sheet.rows.find((r) => r.document_id === S.fix.doc);
      const col = b.sheet.columns.find((c) => c.key === S.fix.col);
      if (!row || !col) return;
      const cell = row.cells[col.key] || {};
      box.innerHTML = `<div class="grid g21"><div class="card"><div class="hd"><h2>Row ${row.ordinal} · ${esc(row.filename)} · ${esc(col.label)}</h2><span class="spin"></span></div></div></div>`;
      let ctxd = { page: cell.page || 1, lines: [], label: '' };
      try { ctxd = await api.get(`/api/pdfstudio/documents/${row.document_id}/context?col=${encodeURIComponent(col.key)}`); } catch { /* keep default */ }
      const snippet = (ctxd.lines.length ? ctxd.lines.map((l) => l.highlight ? `<mark style="background:var(--warn-soft);color:var(--warn);padding:0 4px;border-radius:3px">${esc(l.text)}</mark>${cell.status === 'miss' ? ' <span class="muted">← C-EXA looked here</span>' : ''}` : esc(l.text)).join('<br>') : '<span class="muted">No text on this page — it may be a scan. Open the PDF to read it.</span>') + (ctxd.note ? `<br><span class="muted">${esc(ctxd.note)}</span>` : '');
      const flagged = cell.status === 'flagged';
      box.innerHTML = `
      <div class="grid g21">
        <div class="card"><div class="hd"><h2>Row ${row.ordinal} · ${esc(row.filename)} · ${esc(col.label)}</h2><button class="btn sm" id="open-pdf">Open PDF at page ${ctxd.page || 1}</button></div>
          <div class="bd">
            <div style="border:1px solid var(--line);border-radius:8px;padding:14px;background:var(--surface-2);font-family:var(--mono);font-size:12px;color:var(--ink-2);line-height:1.8">${snippet}</div>
            ${cell.note ? `<p class="small muted" style="margin-top:8px">C-EXA: ${esc(cell.note)}</p>` : ''}
            ${flagged ? `<div class="thinking" style="margin-top:12px"><div class="eyebrow">C-EXA asks</div>I can't find <b>${esc(fmtCell(cell, col))}</b> on this bill — is it from another document, or should I trust you and note the source?<div class="row" style="margin-top:8px;gap:6px"><button class="btn sm" data-ans="other_source">It's from another document</button><button class="btn sm" data-ans="trust_me">Trust me</button><button class="btn sm ghost" data-ans="retract">Take it back</button></div></div>` : ''}
            <div class="row" style="margin-top:12px;gap:10px"><div class="field" style="flex:1"><label>${esc(col.label)}</label><input type="text" id="fix-val" placeholder="Type what you see" value="${cell.status === 'miss' ? '' : esc(cell.value ?? '')}"></div><button class="btn primary" style="align-self:flex-end" id="fix-save">Save</button></div>
            ${admin ? `<div class="thinking" style="margin-top:12px"><div class="eyebrow">C-EXA will check your value</div>If what you type isn't on the PDF, she'll ask whether it's from another document or whether to trust you and note the source. Nothing goes into the master sheet unverified.</div>` : ''}
          </div></div>
        ${admin ? `<div class="card"><div class="hd"><h2>What C-EXA learned</h2></div><ul class="list">
          ${S.learned ? `<li><div class="t"><b>Profile v${S.learned.version - 1} → v${S.learned.version} pending</b><span>${esc(S.learned.reason)} · back-tested on ${S.learned.backtest.docs} docs · ${S.learned.backtest.passed} pass · awaiting approval</span></div></li>` : `<li><div class="t"><b>Your fixes become rules</b><span>Each verified fill is back-tested on every other bill of this layout; if nothing regresses it becomes a pending profile version for an admin to approve.</span></div></li>`}
          ${(b.profiles || []).map((p) => `<li><div class="t"><b>${esc(p.label)}</b><span>v${p.version} · ${p.docs} doc${p.docs === 1 ? '' : 's'} in this batch${p.accuracy != null ? ` · ${(p.accuracy * 100).toFixed(1)}%` : ''}</span></div>${pill(p.status === 'live' ? 'Live' : p.status === 'patch' ? 'Patch' : p.status, p.status === 'live' ? 'good' : p.status === 'patch' ? 'warn' : 'neutral')}</li>`).join('')}
          <li><div class="t"><b>Shared with</b><span>${esc(ctx.me.team || 'your team')} · profiles live in the shared library</span></div></li>
        </ul></div>` : ''}
      </div>`;
      box.querySelector('#open-pdf').onclick = () => {
        const p = ctxd.page || 1;
        const m = modal({ title: `${row.filename} · page ${p} of ${ctxd.pages || row.pages || '?'}`, body: `<img src="/api/pdfstudio/documents/${row.document_id}/page/${p}.png" alt="page ${p}" style="width:100%;border:1px solid var(--line);border-radius:8px">` });
        m.root.querySelector('.modal').style.maxWidth = '900px';
      };
      const save = async () => {
        const v = box.querySelector('#fix-val').value.trim(); if (!v) { toast('Type a value first', 'err'); return; }
        try {
          const r = await api.post(`/api/pdfstudio/batches/${b.id}/cells`, { document_id: row.document_id, column_key: col.key, value: v });
          if (r.learned && typeof r.learned === 'object') S.learned = r.learned;
          S.batch = await api.get(`/api/pdfstudio/batches/${b.id}`);
          if (r.verified) { toast(`Saved · C-EXA found it on page ${r.cell.page}`); ctx.refreshCounts && ctx.refreshCounts(); draw(); }
          else { draw(); askVerification(r, row, col); }
        } catch (e) { toast(e.detail || 'Could not save', 'err'); }
      };
      box.querySelector('#fix-save').onclick = save;
      box.querySelector('#fix-val').onkeydown = (e) => { if (e.key === 'Enter') save(); };
      box.querySelectorAll('[data-ans]').forEach((btn) => (btn.onclick = () => answer(cell.correction_id, btn.dataset.ans, row, col)));
    }
    function askVerification(r, row, col) {
      const body = el(`<div style="display:flex;flex-direction:column;gap:12px"><p>${esc(r.question)}</p><div class="field"><label>Where does it come from? (needed if it's from another document)</label><input type="text" id="v-note" placeholder="e.g. the deposit register, June statement"></div></div>`);
      const m = modal({ title: 'C-EXA can\'t find that on the PDF', body, footer: r.options.map((o) => `<button class="btn ${o.id === 'retract' ? 'ghost' : o.id === 'trust_me' ? '' : 'primary'}" data-o="${o.id}">${esc(o.label)}</button>`).join('') });
      m.root.querySelectorAll('[data-o]').forEach((btn) => (btn.onclick = async () => {
        const note = body.querySelector('#v-note').value.trim();
        if (btn.dataset.o === 'other_source' && !note) { toast('Tell C-EXA where the value comes from', 'err'); return; }
        m.close(); await answer(r.correction_id, btn.dataset.o, row, col, note);
      }));
    }
    async function answer(cid, ans, row, col, note) {
      if (!cid) return;
      if (ans === 'other_source' && note === undefined) {
        const body = el(`<div class="field"><label>Where does it come from?</label><input type="text" id="v-note" placeholder="e.g. the deposit register"></div>`);
        const m = modal({ title: 'From another document', body, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="v-ok">Save</button>' });
        m.root.querySelector('#v-ok').onclick = () => { const n = body.querySelector('#v-note').value.trim(); if (!n) { toast('A note is needed', 'err'); return; } m.close(); answer(cid, ans, row, col, n); };
        return;
      }
      try {
        const r = await api.post(`/api/pdfstudio/batches/${S.batch.id}/cells/answer`, { correction_id: cid, answer: ans, note: note || '' });
        S.batch = await api.get(`/api/pdfstudio/batches/${S.batch.id}`);
        toast(r.status === 'trusted' ? 'Noted · the value is marked as trusted with your note' : 'Taken back');
        ctx.refreshCounts && ctx.refreshCounts(); draw();
      } catch (e) { toast(e.detail || 'Could not save', 'err'); }
    }

    draw();
    if (running()) startPolling();
    return dispose;
  },
};
