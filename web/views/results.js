// Results: finished batches and result files. Users see their own; admins see everyone's.
import { api } from '../api.js';
import { esc, statusPill, fmtDate, fmtNum } from '../ui.js';
import { pdfSegHtml, pdfSegWire } from './pdf-seg.js';

const fico = (kind) => `<div class="fico ${kind === 'xlsx' ? 'xls' : kind === 'pdf' ? 'pdf' : kind === 'csv' ? 'csv' : 'doc'}">${kind === 'xlsx' ? 'XLSX' : (kind || 'file').toUpperCase().slice(0, 4)}</div>`;

// 1.0.83: a tab of PDF Studio (it was under Requests, where it did not belong)
export default {
  title: 'Results',
  navRoute: 'pdf-studio',
  async render(root, ctx) {
    const admin = ctx.me.role !== 'user';
    let d;
    try { d = await api.get('/api/pdfstudio/results'); }
    catch (e) {
      root.innerHTML = `<div class="head"><div><h1>Results</h1><p>${esc(e.detail || e.message)}</p></div></div>`; return;
    }
    const batches = d.batches.map((b) => `<li>${fico('xlsx')}<div class="t"><b>${esc(b.name || 'PDF batch')}</b><span>${b.docs} PDF${b.docs === 1 ? '' : 's'} · ${b.status === 'review' ? `${b.cells_for_user} cell${b.cells_for_user === 1 ? '' : 's'} need you` : 'Ready'} · ${fmtDate(b.finished_at, { rel: true })}${admin && b.owner ? ` · ${esc(b.owner)}` : ''}</span></div>${statusPill(b.status)}<button class="btn sm primary" data-open="${b.id}">Open</button><a class="btn sm" href="/api/pdfstudio/batches/${b.id}/export.xlsx">Download</a></li>`).join('');
    const files = d.files.map((f) => `<li>${fico(f.kind)}<div class="t"><b>${esc(f.name)}</b><span>${fmtNum(Math.max(1, Math.round(f.size / 1024)))} KB · ${fmtDate(f.at, { rel: true })}${admin && f.owner ? ` · ${esc(f.owner)}` : ''}</span></div><a class="btn sm" href="${esc(f.download)}">Download</a></li>`).join('');
    root.innerHTML = `
    ${pdfSegHtml(ctx, 'results')}<div class="head"><div><h1>PDF Studio · Results</h1><p>${admin ? "Everyone's finished batches and result files." : 'Your finished batches and the files they produced. Only you can see these.'}</p></div></div>
    <div class="grid g2">
      <div class="card"><div class="hd"><h2>Finished batches</h2><span class="small muted">${d.batches.length}</span></div><ul class="list">${batches || '<li class="empty">Nothing finished yet. Run a batch in PDF Studio and it lands here.</li>'}</ul></div>
      <div class="card"><div class="hd"><h2>Result files</h2><span class="small muted">${d.files.length}</span></div><ul class="list">${files || '<li class="empty">No files yet. "Download Excel" on a finished batch saves a copy here.</li>'}</ul></div>
    </div>`;
    root.querySelectorAll('[data-open]').forEach((b) => (b.onclick = () => ctx.go('pdf-studio', { batch: b.dataset.open })));
    pdfSegWire(root, ctx, 'results');
  },
};
