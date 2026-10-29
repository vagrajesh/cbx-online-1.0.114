// Master Sheets: living workbooks. Cards → open a sheet (rows you may see) → download / exceptions.
import { api } from '../api.js';
import { esc, toast, modal, pill, fmtDate, fmtNum, fmtMoney, el } from '../ui.js';
import { pdfSegHtml, pdfSegWire } from './pdf-seg.js';

const TYPES = ['text', 'number', 'money', 'date', 'list'];

function fmt(v, ctype) {
  if (v === null || v === undefined || v === '') return '';
  if (ctype === 'money') return fmtMoney(v);
  if (ctype === 'number') return fmtNum(v, Number.isInteger(Number(v)) ? 0 : 2);
  return String(v);
}

export default {
  title: 'PDF Studio · Master sheets',
  navHighlight: 'pdf-studio',
  async render(root, ctx) {
    const admin = ctx.me.role !== 'user';
    let list;
    try { list = await api.get('/api/master-sheets'); } catch (e) { root.innerHTML = `<div class="head"><div><h1>Master Sheets</h1><p>${esc(e.detail || e.message)}</p></div></div>`; return; }
    let open = ctx.params.id ? Number(ctx.params.id) : null;
    let sheet = null;
    let tab = 'rows';

    async function load() { sheet = open ? await api.get(`/api/master-sheets/${open}`) : null; }
    await load();

    function drawList() {
      root.innerHTML = `
      ${pdfSegHtml(ctx, 'master-sheets')}
      <div class="head"><div><h1>PDF Studio</h1><p>Master sheets: one running workbook per kind of document. Each batch you send, and each harvest, adds its rows here.</p></div>${admin ? '<button class="btn primary sm" id="new-sheet">New sheet</button>' : ''}</div>
      ${list.sheets.length ? `<div class="grid g3">${list.sheets.map((s) => `
        <div class="card ${s.is_active ? '' : 'muted'}"><div class="bd"><div class="row between"><div class="fico xls">XLSX</div>${pill('Updated ' + fmtDate(s.updated_at, { rel: true }), recent(s.updated_at) ? 'good' : 'neutral')}</div>
          <h2 style="margin-top:14px">${esc(s.name)}</h2><p class="small muted" style="margin:4px 0 14px">${esc(s.description || '')}${s.description ? ' · ' : ''}${fmtNum(s.rows)} row${s.rows === 1 ? '' : 's'}${admin && s.rows_total !== s.rows ? ` (${fmtNum(s.rows_total)} total)` : ''}${s.exceptions ? ` · <span style="color:var(--warn)">${s.exceptions} exception${s.exceptions === 1 ? '' : 's'}</span>` : ''}${s.is_active ? '' : ' · archived'}</p>
          <div class="row"><button class="btn sm primary" data-open="${s.id}">Open</button><a class="btn sm" href="/api/master-sheets/${s.id}/export.xlsx">Download</a></div></div></div>`).join('')}</div>`
        : '<div class="card"><div class="empty">No master sheets yet.</div></div>'}`;
      root.querySelectorAll('[data-open]').forEach((b) => (b.onclick = async () => { open = Number(b.dataset.open); await load(); draw(); }));
      const ns = root.querySelector('#new-sheet'); if (ns) ns.onclick = () => editSheet(null);
      pdfSegWire(root, ctx, 'master-sheets');
    }
    const recent = (iso) => iso && (Date.now() - new Date(iso)) < 86400000;

    function drawSheet() {
      const s = sheet;
      const cols = s.columns || [];
      const rows = tab === 'rows' ? s.data : s.exception_rows;
      root.innerHTML = `
      ${pdfSegHtml(ctx, 'master-sheets')}
      <div class="head"><div><h1>${esc(s.name)}</h1><p>${esc(s.description || '')} ${fmtNum(s.rows)} row${s.rows === 1 ? '' : 's'} you can see${admin && s.rows_total !== s.rows ? ` of ${fmtNum(s.rows_total)}` : ''} · updated ${fmtDate(s.updated_at, { rel: true })}</p></div>
        <div class="row"><button class="btn sm" id="back">All sheets</button>${admin ? '<button class="btn sm" id="edit-cols">Edit columns</button>' : ''}<a class="btn sm primary" href="/api/master-sheets/${s.id}/export.xlsx">Download Excel</a></div></div>
      <div class="tabs"><button aria-selected="${tab === 'rows'}" data-tab="rows">Rows (${s.rows})</button><button aria-selected="${tab === 'exc'}" data-tab="exc">Exceptions (${s.exceptions})</button></div>
      <div class="card"><div class="tbl sheet"><table>
        <thead><tr><th>#</th>${cols.map((c) => `<th>${esc(c.label)}</th>`).join('')}<th>Added by</th><th>When</th>${tab === 'exc' ? '<th>Why</th>' : ''}</tr></thead>
        <tbody>${rows.length ? rows.map((r, i) => `<tr><td class="muted">${i + 1}</td>${cols.map((c) => `<td class="${['money', 'number'].includes(c.ctype) ? 'num' : ''}">${esc(fmt(r[c.key], c.ctype))}</td>`).join('')}<td class="small muted">${esc(r._by_name || r._by || '')}</td><td class="small muted">${fmtDate(r._at)}</td>${tab === 'exc' ? `<td class="small" style="color:var(--warn)">${esc(r._why || '')}</td>` : ''}</tr>`).join('') : `<tr><td colspan="${cols.length + 4}" class="empty">${tab === 'rows' ? 'No rows you can see yet.' : 'No exceptions.'}</td></tr>`}</tbody>
      </table></div>
      ${!admin ? '<div class="bd small muted" style="border-top:1px solid var(--line)">You see the rows your access rules allow and the rows you added. The download contains only those.</div>' : ''}</div>`;
      root.querySelector('#back').onclick = () => { open = null; sheet = null; ctx.go('master-sheets'); draw(); };
      pdfSegWire(root, ctx, 'master-sheets');
      root.querySelectorAll('[data-tab]').forEach((b) => (b.onclick = () => { tab = b.dataset.tab; draw(); }));
      const ec = root.querySelector('#edit-cols'); if (ec) ec.onclick = () => editSheet(s);
    }

    function editSheet(s) {
      const cols = s ? (s.columns || []).map((c) => ({ ...c })) : [{ key: '', label: '', ctype: 'text' }]; // copies: Cancel must not change the sheet on screen
      const body = el(`<div style="display:flex;flex-direction:column;gap:12px">
        ${s ? '' : '<div class="field"><label>Name</label><input type="text" id="s-name" placeholder="e.g. Eskom demand"></div><div class="field"><label>Description</label><input type="text" id="s-desc" placeholder="What lives here"></div>'}
        <div class="eyebrow">Columns</div><div id="s-cols"></div><button class="btn sm" id="s-add">+ Add column</button></div>`);
      const drawCols = () => {
        body.querySelector('#s-cols').innerHTML = cols.map((c, i) => `<div class="row" style="gap:8px;margin-bottom:6px"><input type="text" data-l="${i}" value="${esc(c.label)}" placeholder="Label" style="flex:1"><select data-t="${i}" style="width:auto">${TYPES.map((t) => `<option ${c.ctype === t ? 'selected' : ''}>${t}</option>`).join('')}</select><button class="btn sm ghost" data-x="${i}">Remove</button></div>`).join('');
        body.querySelectorAll('[data-l]').forEach((inp) => (inp.oninput = () => { cols[Number(inp.dataset.l)].label = inp.value; }));
        body.querySelectorAll('[data-t]').forEach((sel) => (sel.onchange = () => { cols[Number(sel.dataset.t)].ctype = sel.value; }));
        body.querySelectorAll('[data-x]').forEach((b) => (b.onclick = () => { cols.splice(Number(b.dataset.x), 1); drawCols(); }));
      };
      drawCols();
      body.querySelector('#s-add').onclick = () => { cols.push({ key: '', label: '', ctype: 'text' }); drawCols(); };
      const m = modal({ title: s ? `Edit columns · ${s.name}` : 'New master sheet', body, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="s-ok">Save</button>' });
      m.root.querySelector('#s-ok').onclick = async () => {
        const payload = cols.filter((c) => c.label.trim()).map((c) => ({ key: c.key || '', label: c.label.trim(), ctype: c.ctype }));
        try {
          if (s) { await api.patch(`/api/master-sheets/${s.id}`, { columns: payload }); }
          else { const name = body.querySelector('#s-name').value.trim(); if (!name) { toast('Give the sheet a name', 'err'); return; } const r = await api.post('/api/master-sheets', { name, description: body.querySelector('#s-desc').value.trim(), columns: payload }); open = r.id; }
          m.close(); list = await api.get('/api/master-sheets'); await load(); toast('Saved · logged'); draw();
        } catch (e) { toast(e.detail || 'Could not save', 'err'); }
      };
    }

    function draw() { if (sheet) drawSheet(); else drawList(); }
    draw();
  },
};
