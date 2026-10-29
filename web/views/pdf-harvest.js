// PDF Studio → Harvest: folders watched continuously. Users pick a source and a date range and get
// the rows (with the PDF behind each). Admins add sources, run now, pause, edit, re-run a range.
import { api } from '../api.js';
import { esc, toast, modal, confirmBox, pill, fmtDate, fmtNum, fmtMoney, el } from '../ui.js';
import { pdfSegHtml, pdfSegWire } from './pdf-seg.js';

const POLL_MS = 3000;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const KIND_LABEL = { local: 'Folder on the server or a network share', onedrive: 'OneDrive folder' };

function iso(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }
function monthRange(offset) { const n = new Date(); const a = new Date(n.getFullYear(), n.getMonth() + offset, 1); const b = new Date(n.getFullYear(), n.getMonth() + offset + 1, 0); return [iso(a), iso(b)]; }
function fmtIso(s) { if (!s) return ''; const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s); return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}` : s; }
function fmtCell(cell, col) {
  const v = cell && cell.value;
  if (v === null || v === undefined || v === '') return '';
  if (col.ctype === 'money') return fmtMoney(v);
  if (col.ctype === 'number') return fmtNum(v, Number.isInteger(Number(v)) ? 0 : 2);
  if (col.ctype === 'date') { const parts = String(v).split(' – '); return parts.map(fmtIso).join(' – '); }
  return String(v);
}
function statusPill(s) {
  const map = { ok: ['Up to date', 'good'], running: ['Reading', 'ai', true], error: ['Problem', 'crit'], paused: ['Paused', 'neutral'], idle: ['Not run yet', 'neutral'] };
  const [t, k, r] = map[s] || [s, 'neutral'];
  return pill(t, k, r);
}

export default {
  title: 'PDF Studio · Harvest',
  navRoute: 'pdf-studio',
  async render(root, ctx) {
    const admin = ctx.me.role !== 'user';
    const S = { sources: [], can: false, onedrive: null, sel: null, from: '', to: '', preset: 'this', q: '', rows: null, loading: false, timer: null, templates: [] };
    [S.from, S.to] = monthRange(0);
    const dispose = () => { if (S.timer) clearInterval(S.timer); S.timer = null; };

    async function loadSources(quiet) {
      try {
        const r = await api.get('/api/pdfstudio/harvest/sources', { quiet: !!quiet });
        S.sources = r.sources; S.can = r.can_manage; S.onedrive = r.onedrive;
      } catch (e) { if (!quiet) toast(e.detail || 'Could not load the harvest sources', 'err'); }
      if (S.sel && !S.sources.find((s) => s.id === S.sel)) S.sel = null;
      if (!S.sel && S.sources.length) S.sel = Number(ctx.params.source) && S.sources.find((s) => s.id === Number(ctx.params.source)) ? Number(ctx.params.source) : S.sources[0].id;
    }
    async function loadRows() {
      if (!S.sel) { S.rows = null; return; }
      S.loading = true;
      const p = new URLSearchParams();
      if (S.from) p.set('from', S.from);
      if (S.to) p.set('to', S.to);
      if (S.q) p.set('q', S.q);
      p.set('size', '300');
      try { S.rows = await api.get(`/api/pdfstudio/harvest/sources/${S.sel}/rows?${p}`, { quiet: true }); }
      catch (e) { S.rows = null; toast(e.detail || 'Could not load the rows', 'err'); }
      S.loading = false;
    }
    async function loadTemplates() { if (!admin) return; try { S.templates = (await api.get('/api/pdfstudio/templates', { quiet: true })).templates; } catch { S.templates = []; } }

    await Promise.all([loadSources(), loadTemplates()]);
    await loadRows();

    const anyRunning = () => S.sources.some((s) => s.status === 'running');
    function ensurePolling() {
      if (anyRunning() && !S.timer) S.timer = setInterval(async () => {
        const was = S.sources.map((s) => `${s.id}:${s.status}:${s.rows}`).join(',');
        await loadSources(true);
        const now = S.sources.map((s) => `${s.id}:${s.status}:${s.rows}`).join(',');
        if (was !== now) { await loadRows(); draw(); }
        if (!anyRunning()) { clearInterval(S.timer); S.timer = null; }
      }, POLL_MS);
    }

    // ---- drawing ------------------------------------------------------------------------------------
    function draw() {
      const sel = S.sources.find((s) => s.id === S.sel) || null;
      root.innerHTML = `
      ${pdfSegHtml(ctx, 'pdf-harvest')}
      <div class="head"><div><h1>PDF Studio</h1><p>Point it at a folder. Every new PDF is read and kept as a row you can pull by date.</p></div>
        <div class="row">${admin && S.can && S.sources.length ? '<button class="btn primary sm" id="add-src">Add source</button>' : ''}</div></div>
      ${S.sources.length ? `<div class="grid g3 hv-cards">${S.sources.map(cardHtml).join('')}</div>` : emptyHtml()}
      ${sel ? rowsHtml(sel) : ''}`;
      pdfSegWire(root, ctx, 'pdf-harvest', dispose);
      root.querySelectorAll('#add-src,#add-src2').forEach((b) => (b.onclick = () => editSource(null)));
      root.querySelectorAll('[data-sel]').forEach((c) => (c.onclick = async (e) => { if (e.target.closest('button,a')) return; S.sel = Number(c.dataset.sel); await loadRows(); draw(); }));
      root.querySelectorAll('[data-run]').forEach((b) => (b.onclick = () => runNow(Number(b.dataset.run), b)));
      root.querySelectorAll('[data-pause]').forEach((b) => (b.onclick = () => togglePause(Number(b.dataset.pause))));
      root.querySelectorAll('[data-edit]').forEach((b) => (b.onclick = () => editSource(S.sources.find((s) => s.id === Number(b.dataset.edit)))));
      root.querySelectorAll('[data-rerun]').forEach((b) => (b.onclick = () => rerun(S.sources.find((s) => s.id === Number(b.dataset.rerun)))));
      wireRows(sel);
      ensurePolling();
    }

    function emptyHtml() {
      if (admin && S.can) {
        return `<div class="card"><div class="bd"><b>Nothing is being harvested yet.</b>
          <p class="small muted" style="margin:6px 0 12px;max-width:62ch">Add a source: a name (the client or supplier, e.g. Telkom), the template that says which columns to fill, and the folder the PDFs land in — on this server, a network share, or OneDrive. From then on it runs by itself: new PDFs become rows, and anyone with access can pull a date range here.</p>
          <button class="btn primary sm" id="add-src2">Add the first source</button></div></div>`;
      }
      return `<div class="card"><div class="bd"><b>Nothing is being harvested for you yet.</b><p class="small muted" style="margin-top:6px;max-width:62ch">Harvest watches a folder and reads every PDF that lands in it, so you can pull a month's figures in one click. Ask an admin to add a source for your team — they need the folder path and the template (which columns you want).</p></div></div>`;
    }

    function cardHtml(s) {
      const on = s.id === S.sel;
      const next = s.status === 'paused' ? 'paused' : s.status === 'running' ? 'reading new PDFs…' : s.last_run_at ? `last run ${fmtDate(s.last_run_at, { rel: true })} · again every ${s.every_minutes} min` : 'first run soon';
      const sub = `${fmtNum(s.rows)} row${s.rows === 1 ? '' : 's'}${s.first_date ? ` · ${fmtIso(s.first_date)} – ${fmtIso(s.last_date)}` : ''}${s.review ? ` · <span style="color:var(--warn)">${s.review} need a person</span>` : ''}`;
      const problem = admin ? s.last_error : s.problem;
      return `<div class="card hv-card ${on ? 'on' : ''}" data-sel="${s.id}">
        <div class="bd">
          <div class="row between"><h2 style="margin:0">${esc(s.name)}</h2>${statusPill(s.status)}</div>
          <div class="small muted" style="margin-top:4px">${esc(s.template)} · ${esc(next)}</div>
          <div class="small" style="margin-top:10px">${sub}</div>
          ${problem ? `<div class="small" style="margin-top:8px;color:var(--crit)">${esc(problem)}</div>` : ''}
          ${admin ? `<div class="small mono muted" style="margin-top:8px;overflow-wrap:anywhere">${esc(s.kind === 'onedrive' ? 'OneDrive · ' : '')}${esc(s.path)}${s.include_subfolders ? ' (+subfolders)' : ''}</div>` : ''}
          ${admin && S.can ? `<div class="row" style="margin-top:12px;gap:6px;flex-wrap:wrap">
            <button class="btn sm" data-run="${s.id}" ${s.status === 'running' ? 'disabled' : ''}>Run now</button>
            <button class="btn sm" data-pause="${s.id}">${s.active ? 'Pause' : 'Resume'}</button>
            <button class="btn sm ghost" data-edit="${s.id}">Edit</button>
            <button class="btn sm ghost" data-rerun="${s.id}" ${s.rows ? '' : 'disabled'} title="Re-read a date range with the current profile">Re-run range</button>
          </div>` : ''}
        </div></div>`;
    }

    function rowsHtml(sel) {
      const R = S.rows;
      const cols = R ? R.columns : sel.columns || [];
      const presets = [['this', 'This month'], ['last', 'Last month'], ['all', 'Everything'], ['custom', 'Custom']];
      const dateKey = R && R.date_column;
      const body = !R ? `<tr><td colspan="${cols.length + 2}" class="empty">${S.loading ? 'Loading…' : 'Could not load the rows.'}</td></tr>`
        : !R.rows.length ? `<tr><td colspan="${cols.length + 2}" class="empty">${sel.rows ? `No rows between ${fmtIso(R.from) || 'the start'} and ${fmtIso(R.to) || 'today'}${S.q ? ` matching “${esc(S.q)}”` : ''}.${sel.last_date && !S.q ? ` The latest PDF is dated ${fmtIso(sel.last_date)} — try “Everything” or a custom range.` : ' Try another range.'}` : sel.status === 'running' ? 'Reading the first PDFs — rows appear here as they are done.' : sel.status === 'error' ? 'Nothing read yet — the last run had a problem.' : 'No PDFs have been read yet. They appear here after the first run.'}</td></tr>`
        : R.rows.map((r) => `<tr>
            <td class="small" style="white-space:nowrap">${r.date ? fmtIso(r.date) : '<span class="muted" title="No date found on the PDF">—</span>'}</td>
            <td style="white-space:nowrap"><a class="btn sm" href="${esc(r.open_url)}" target="_blank" rel="noopener" title="${esc(r.filename)}">Open PDF</a>${r.status === 'review' ? ' ' + pill('Needs you', 'warn') : ''}${admin && r.reruns ? ` <span class="small muted" title="re-read ${r.reruns} time${r.reruns === 1 ? '' : 's'} with a newer profile; earlier values are kept">re-read</span>` : ''}</td>
            ${cols.map((c) => cellTd(r, c)).join('')}
          </tr>`).join('');
      const exportUrl = `/api/pdfstudio/harvest/sources/${sel.id}/export.xlsx?${new URLSearchParams({ ...(S.from ? { from: S.from } : {}), ...(S.to ? { to: S.to } : {}) })}`;
      return `
      <div class="card" style="margin-top:16px"><div class="hd" style="flex-wrap:wrap;gap:10px">
          <h2>${esc(sel.name)}</h2>
          <div class="row" style="gap:8px;flex-wrap:wrap">
            <div class="segctl sm" style="margin:0">${presets.map(([k, l]) => `<button aria-selected="${S.preset === k}" data-preset="${k}">${l}</button>`).join('')}</div>
            <input type="date" id="hv-from" value="${esc(S.from)}" style="width:auto" ${S.preset === 'custom' ? '' : 'disabled'}>
            <span class="small muted">to</span>
            <input type="date" id="hv-to" value="${esc(S.to)}" style="width:auto" ${S.preset === 'custom' ? '' : 'disabled'}>
            <input type="text" id="hv-q" placeholder="Search" value="${esc(S.q)}" style="width:150px">
            <a class="btn sm primary" href="${exportUrl}">Export Excel</a>
            ${sel.master_sheet_id ? `<button class="btn sm ghost" id="hv-master">Master sheet</button>` : ''}
          </div></div>
        <div class="tbl sheet"><table>
          <thead><tr><th>Date</th><th></th>${cols.map((c) => `<th>${esc(c.label)}${dateKey === c.key ? ' <span class="muted" title="Rows are filed by this column">·</span>' : ''}</th>`).join('')}</tr></thead>
          <tbody>${body}</tbody></table></div>
        <div class="bd small muted" style="border-top:1px solid var(--line)">${R ? `${fmtNum(R.total)} row${R.total === 1 ? '' : 's'}${R.total > R.rows.length ? ` · showing the first ${R.rows.length} — narrow the range or export for all` : ''}${R.undated ? ` · ${R.undated} without a date on the PDF (filed by the file's date)` : ''}. ` : ''}Rows are read once and kept, so a range comes back at once. <span style="color:var(--warn);font-weight:600">Orange</span> = the cell needs a person${admin ? ' — open the batch in Batches to fill it in' : ''}.</div>
      </div>`;
    }
    function cellTd(r, c) {
      const cell = r.cells[c.key] || { status: 'miss' };
      const numeric = c.ctype === 'money' || c.ctype === 'number';
      if (cell.status === 'miss' || cell.status === 'flagged') return `<td class="miss" title="${esc(cell.note || 'not found on the PDF')}">${cell.value != null ? '? ' + esc(fmtCell(cell, c)) : '?'}</td>`;
      return `<td class="${numeric ? 'num' : c.key.includes('account') ? 'mono' : ''}" title="${admin ? `page ${cell.page || '?'} · ${esc(cell.quote || '')}` : cell.page ? `page ${cell.page}` : ''}">${esc(fmtCell(cell, c))}</td>`;
    }
    function wireRows(sel) {
      if (!sel) return;
      root.querySelectorAll('[data-preset]').forEach((b) => (b.onclick = async () => {
        S.preset = b.dataset.preset;
        if (S.preset === 'this') [S.from, S.to] = monthRange(0);
        else if (S.preset === 'last') [S.from, S.to] = monthRange(-1);
        else if (S.preset === 'all') { S.from = ''; S.to = ''; }
        await loadRows(); draw();
      }));
      const f = root.querySelector('#hv-from'), t = root.querySelector('#hv-to'), q = root.querySelector('#hv-q');
      if (f) f.onchange = async () => { S.from = f.value; await loadRows(); draw(); };
      if (t) t.onchange = async () => { S.to = t.value; await loadRows(); draw(); };
      if (q) { let tm; q.oninput = () => { clearTimeout(tm); tm = setTimeout(async () => { S.q = q.value.trim(); const pos = q.selectionStart; await loadRows(); draw(); const nq = root.querySelector('#hv-q'); if (nq) { nq.focus(); nq.setSelectionRange(pos, pos); } }, 350); }; }
      const ms = root.querySelector('#hv-master'); if (ms) ms.onclick = () => { dispose(); ctx.go('master-sheets', { id: sel.master_sheet_id }); };
    }

    // ---- admin actions --------------------------------------------------------------------------------
    async function runNow(id, btn) {
      if (btn) btn.disabled = true;
      try {
        const r = await api.post(`/api/pdfstudio/harvest/sources/${id}/run`);
        toast(r.started ? 'Started — new PDFs will show up as rows in a few minutes' : 'Already running');
        await loadSources(true); await loadRows(); draw();
      } catch (e) { if (btn) btn.disabled = false; toast(e.detail || 'Could not start', 'err'); }
    }
    async function togglePause(id) {
      const s = S.sources.find((x) => x.id === id);
      try {
        await api.patch(`/api/pdfstudio/harvest/sources/${id}`, { active: !s.active });
        toast(s.active ? `${s.name} paused — nothing new is read until you resume` : `${s.name} resumed`);
        await loadSources(true); draw();
      } catch (e) { toast(e.detail || 'Could not change that', 'err'); }
    }
    function editSource(s) {
      const tpls = S.templates;
      if (!tpls.length) { toast('Make a template first (Batches → Template) so Harvest knows which columns to fill', 'err'); return; }
      const tid0 = s ? s.template_id : tpls[0].id;
      const dateCols = (tid) => ((tpls.find((t) => t.id === tid) || {}).columns || []).filter((c) => c.ctype === 'date');
      const od = S.onedrive || {};
      const body = el(`<div style="display:flex;flex-direction:column;gap:12px">
        <div class="field"><label>Name</label><input type="text" id="s-name" value="${esc(s ? s.name : '')}" placeholder="e.g. Telkom — one source per client or supplier"></div>
        <div class="field"><label>Template (which columns to fill)</label><select id="s-tpl" ${s && s.rows ? 'disabled' : ''}>${tpls.map((t) => `<option value="${t.id}" ${t.id === tid0 ? 'selected' : ''}>${esc(t.name)} · ${t.columns.length} columns</option>`).join('')}</select>${s && s.rows ? '<span class="small muted">Rows have been read with this template already. Add a new source for a different template.</span>' : ''}</div>
        <div class="field"><label>Where the PDFs land</label><select id="s-kind">${Object.entries(KIND_LABEL).map(([k, l]) => `<option value="${k}" ${(s ? s.kind : 'local') === k ? 'selected' : ''}>${l}${k === 'onedrive' && !od.connected ? ' (not connected on this server)' : ''}</option>`).join('')}</select></div>
        <div class="field"><label id="s-path-l">Folder path</label><div class="row" style="gap:8px"><input type="text" id="s-path" value="${esc(s ? s.path : '')}" placeholder="C:\\Bills\\Telkom  or  \\\\server\\share\\Telkom"><button class="btn sm" id="s-check" style="flex:none">Check folder</button></div><div class="small muted" id="s-check-out"></div></div>
        <label class="row small" style="gap:8px"><input type="checkbox" id="s-subs" ${s ? (s.include_subfolders ? 'checked' : '') : 'checked'} style="width:auto"> Include subfolders</label>
        <div class="row" style="gap:12px;flex-wrap:wrap">
          <div class="field" style="flex:1"><label>Look for new PDFs every</label><div class="row" style="gap:8px"><input type="number" id="s-every" min="1" max="1440" value="${s ? s.every_minutes : 15}" style="width:90px"><span class="small muted">minutes</span></div></div>
          <div class="field" style="flex:1"><label>File rows by</label><select id="s-date"><option value="">Automatic (first date column)</option>${dateCols(tid0).map((c) => `<option value="${c.key}" ${s && s.date_column === c.key ? 'selected' : ''}>${esc(c.label)}</option>`).join('')}</select></div>
        </div>
        <label class="row small" style="gap:8px"><input type="checkbox" id="s-active" ${s ? (s.active ? 'checked' : '') : 'checked'} style="width:auto"> Active — runs by itself</label>
      </div>`);
      const m = modal({ title: s ? `Edit source · ${s.name}` : 'Add a harvest source', body, footer: `<button class="btn" data-close>Cancel</button><button class="btn primary" id="s-ok">${s ? 'Save' : 'Add and run now'}</button>` });
      const kindSel = body.querySelector('#s-kind');
      const relabel = () => { const k = kindSel.value; body.querySelector('#s-path-l').textContent = k === 'onedrive' ? `OneDrive folder (under “${od.root || 'CBX Warehouse'}”)` : 'Folder path'; body.querySelector('#s-path').placeholder = k === 'onedrive' ? 'e.g. Bills/Telkom' : 'C:\\Bills\\Telkom  or  \\\\server\\share\\Telkom'; };
      kindSel.onchange = relabel; relabel();
      body.querySelector('#s-tpl').onchange = (e) => { const sel = body.querySelector('#s-date'); sel.innerHTML = `<option value="">Automatic (first date column)</option>${dateCols(Number(e.target.value)).map((c) => `<option value="${c.key}">${esc(c.label)}</option>`).join('')}`; };
      body.querySelector('#s-check').onclick = async () => {
        const out = body.querySelector('#s-check-out'); out.textContent = 'Looking…';
        try { const r = await api.post('/api/pdfstudio/harvest/check-folder', { kind: kindSel.value, path: body.querySelector('#s-path').value, include_subfolders: body.querySelector('#s-subs').checked }); out.textContent = r.message + (r.sample && r.sample.length ? ` e.g. ${r.sample.slice(0, 3).join(', ')}` : ''); out.style.color = r.ok ? 'var(--good)' : 'var(--crit)'; }
        catch (e) { out.textContent = e.detail || 'Could not check'; out.style.color = 'var(--crit)'; }
      };
      m.root.querySelector('#s-ok').onclick = async () => {
        const payload = { name: body.querySelector('#s-name').value.trim(), template_id: Number(body.querySelector('#s-tpl').value), kind: kindSel.value, path: body.querySelector('#s-path').value.trim(), include_subfolders: body.querySelector('#s-subs').checked, every_minutes: Number(body.querySelector('#s-every').value) || 15, active: body.querySelector('#s-active').checked, date_column: body.querySelector('#s-date').value };
        if (!payload.name) { toast('Give the source a name', 'err'); return; }
        if (!payload.path) { toast('Type the folder path', 'err'); return; }
        try {
          if (s) { await api.patch(`/api/pdfstudio/harvest/sources/${s.id}`, payload); toast('Saved · logged'); }
          else { const r = await api.post('/api/pdfstudio/harvest/sources', payload); S.sel = r.id; toast(`${r.name} added · logged`); if (payload.active) { try { await api.post(`/api/pdfstudio/harvest/sources/${r.id}/run`); } catch (e) { toast(e.detail || 'Added, but the first run could not start', 'err'); } } }
          m.close(); await loadSources(true); await loadRows(); draw();
        } catch (e) { toast(e.detail || 'Could not save', 'err'); }
      };
    }
    async function rerun(s) {
      const span = S.from || S.to ? `${fmtIso(S.from) || 'the start'} to ${fmtIso(S.to) || 'today'}` : 'every date';
      const n = S.rows && S.sel === s.id ? S.rows.total : s.rows;
      const ok = await confirmBox(`Re-read ${n} row${n === 1 ? '' : 's'} of ${s.name} (${span}) with the current profile? The PDFs are not fetched again — the stored copies are read with today's rules. Earlier values are kept in each row's history, and the master sheet rows are updated in place.`, 'Re-run');
      if (!ok) return;
      const p = new URLSearchParams({ ...(S.from ? { from: S.from } : {}), ...(S.to ? { to: S.to } : {}) });
      try { const r = await api.post(`/api/pdfstudio/harvest/sources/${s.id}/rerun?${p}`); toast(`Re-reading ${r.rows} row${r.rows === 1 ? '' : 's'} — they update here as they finish`); await loadSources(true); await loadRows(); draw(); }
      catch (e) { toast(e.detail || 'Could not start the re-run', 'err'); }
    }

    draw();
    return dispose;
  },
};
