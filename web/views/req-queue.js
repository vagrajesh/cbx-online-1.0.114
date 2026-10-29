import { api } from '../api.js';
import { esc, statusPill, pill, fmtDate } from '../ui.js';

const IMP_N = { low: 2, medium: 3, high: 4, critical: 4 };
export const impBar = (imp) => {
  if (!imp) return '<span class="small muted">—</span>';
  const n = IMP_N[imp] || 1; const cls = imp === 'low' ? 'low' : imp === 'medium' ? 'med' : '';
  return `<span class="imp ${cls}">${[0, 1, 2, 3].map((i) => `<i class="${i < n ? 'on' : ''}"></i>`).join('')}</span> <span class="small">${imp[0].toUpperCase() + imp.slice(1)}</span>`;
};
export const confCell = (c, extra = '') => c === null || c === undefined ? '<span class="muted">—</span>' : `<b class="num" ${c < 0.6 ? 'style="color:var(--warn)"' : ''}>${Math.round(c * 100)}%</b> ${c < 0.6 ? '<span class="small muted">needs a human</span>' : extra}`;

export default {
  title: 'Requests',
  async render(root, ctx) {
    const d = await api.get('/api/requests/queue');
    const c = d.counts;
    const interviewCell = (r) => {
      if (!r.interview_mode) return '<span class="muted">—</span>';
      const who = r.interview_mode === 'axl' ? 'C-EXA' : 'Human · ' + (r.assignee ? r.assignee.name.split(' ')[0] : '?');
      return pill(who + (r.interview_status === 'done' ? ' · done' : ''), r.interview_mode === 'axl' ? 'ai' : 'neutral');
    };
    const action = (r) => {
      if (r.status === 'new') return `<button class="btn sm primary" data-go="req-detail" data-id="${r.id}">Review</button>`;
      if (r.status === 'interview' && r.interview_mode === 'human') return `<button class="btn sm" data-go="meeting" data-id="${r.interview_id}">Join meeting</button>`;
      if (r.status === 'interview') return `<button class="btn sm" data-go="messages" data-id="${r.interview_id}" data-key="interview">Watch</button>`;
      if (r.ticket_id) return `<button class="btn sm" data-go="build" data-id="${r.ticket_id}">Open build</button>`;
      return `<button class="btn sm ghost" data-go="req-detail" data-id="${r.id}">Open</button>`;
    };
    const rows = d.requests.map((r) => `<tr>
      <td><b>${esc(r.title)}</b><div class="small muted">#${r.number}${r.requester.team ? ' · ' + esc(r.requester.team) : ''}${r.rule_count ? ` · ${r.rule_count} rule${r.rule_count === 1 ? '' : 's'}` : ''} · ${fmtDate(r.created_at, { rel: true })}</div></td>
      <td>${esc(r.requester.name.split(' ')[0])}</td><td>${impBar(r.importance)}</td><td>${confCell(r.confidence)}</td><td>${interviewCell(r)}</td><td>${statusPill(r.status)}</td><td style="text-align:right">${action(r)}</td></tr>`).join('');
    root.innerHTML = `
    <div class="head"><div><h1>Requests</h1><p>C-EXA scores each request and says how confident she is. You approve and choose who interviews.</p></div></div>
    <div class="grid" style="grid-template-columns:repeat(5,1fr);margin-bottom:16px">
      <div class="card stat"><div class="eyebrow">New</div><div class="v num">${c.new}</div><div class="d">Scored, waiting for approval</div></div>
      <div class="card stat"><div class="eyebrow">Interview</div><div class="v num">${c.interview}</div><div class="d">Detail being gathered</div></div>
      <div class="card stat"><div class="eyebrow">Working</div><div class="v num">${c.working}</div><div class="d">Being built</div></div>
      <div class="card stat"><div class="eyebrow">Testing</div><div class="v num">${c.testing}</div><div class="d">In a sandbox</div></div>
      <div class="card stat"><div class="eyebrow">Ready</div><div class="v num">${c.ready}</div><div class="d">Live</div></div>
    </div>
    <div class="card"><div class="tbl"><table><thead><tr><th>Request</th><th>From</th><th>Importance</th><th>C-EXA can do it</th><th>Interview</th><th>Status</th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="7" class="empty">No requests yet. When someone sends one, C-EXA scores it and it lands here.</td></tr>'}</tbody></table></div></div>`;
    root.querySelectorAll('[data-go]').forEach((b) => (b.onclick = () => ctx.go(b.dataset.go, { [b.dataset.key || 'id']: b.dataset.id })));
  },
};
