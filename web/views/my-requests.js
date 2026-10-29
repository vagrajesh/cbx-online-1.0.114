import { api } from '../api.js';
import { esc, statusPill, fmtDate, cancelRequestDialog } from '../ui.js';

const KIND = { filled_sheet: 'Filled sheet', excel: 'Excel file', pdf_report: 'PDF report', check: 'Check & flag' };

export default {
  title: 'My requests',
  async render(root, ctx) {
    let d;
    try { d = await api.get('/api/requests/mine'); }
    catch (e) { root.innerHTML = `<div class="head"><div><h1>My requests</h1><p>${esc(e.detail || 'Could not load your requests. Try again in a moment.')}</p></div></div>`; return; }
    const rows = d.requests.map((r) => {
      const who = r.assignee && ['working', 'testing'].includes(r.status) ? ` · ${esc(r.assignee.name.split(' ')[0])}` : '';
      const open = !['declined', 'cancelled'].includes(r.status);
      const action = (r.interview_id ? `<button class="btn sm ghost" data-chat="${r.interview_id}">Chat</button>` : '') + (open ? `<button class="btn sm ghost" data-cancel="${r.id}" title="Cancel this request">Cancel</button>` : '');
      return `<tr class="link" data-open="${r.id}" title="Open the request"><td><b>${esc(r.title)}</b><div class="small muted mono">#${r.number}</div></td><td class="muted">${esc(KIND[r.result_kind] || '—')}</td><td class="muted">${fmtDate(r.created_at, { rel: true })}</td><td>${statusPill(r.status).replace('</span>', who + '</span>')}</td><td style="text-align:right">${action}</td></tr>`;
    }).join('');
    root.innerHTML = `
    <div class="head"><div><h1>My requests</h1><p>Everything you've asked for, newest first.</p></div></div>
    <div class="card"><div class="tbl"><table><thead><tr><th>Request</th><th>You get</th><th>Sent</th><th>Status</th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5" class="empty">Nothing yet. Start with a new request — it takes about three minutes.</td></tr>'}</tbody></table></div></div>`;
    root.querySelectorAll('[data-chat]').forEach((b) => (b.onclick = (e) => { e.stopPropagation(); ctx.go('messages', { interview: b.dataset.chat }); }));
    // 1.0.83: a row opens the whole request (it looked clickable and was not)
    root.querySelectorAll('tr[data-open]').forEach((tr) => (tr.onclick = (e) => { if (e.target.closest('button, a')) return; ctx.go('req-detail', { id: tr.dataset.open }); }));
    root.querySelectorAll('[data-cancel]').forEach((b) => (b.onclick = async () => { const r = d.requests.find((x) => x.id === Number(b.dataset.cancel)); if (await cancelRequestDialog(api, r)) { ctx.refreshCounts && ctx.refreshCounts(); this.render(root, ctx); } }));
  },
};
