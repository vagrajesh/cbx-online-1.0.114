import { api } from '../api.js';
import { esc, pill, fmtDate } from '../ui.js';

export default {
  title: 'Audit trail',
  async render(root, ctx) {
    let aiOnly = false, q = '', action = '';
    // a manager's trail is their team's people (the server filters); the AI-only switch has nothing to show them
    const isManager = ctx && ctx.me && ctx.me.role === 'manager';
    const ai = (ctx && ctx.ai) || 'C-EXA';
    const draw = async () => {
      const d = await api.get(`/api/audit?limit=300&ai_only=${aiOnly}&q=${encodeURIComponent(q)}&action=${encodeURIComponent(action)}`);
      root.innerHTML = `
      <div class="head"><div><h1>Audit trail</h1><p>${isManager ? `Every action by the people on your team${ctx.me.team ? ` (${esc(ctx.me.team)})` : ''}, and your own.` : `Every action by every person and by ${esc(ai)}.`} Nothing here can be edited or deleted.</p></div>
        <div class="row"><input type="text" id="q" placeholder="Search…" value="${esc(q)}" style="width:200px;max-width:100%"><select id="action" style="width:auto;max-width:100%"><option value="">All actions</option>${d.actions.map((a) => `<option value="${esc(a)}" ${a === action ? 'selected' : ''}>${esc((d.action_words || {})[a] || a)}</option>`).join('')}</select>${isManager ? '' : `<button class="btn sm ${aiOnly ? 'primary' : ''}" id="ai">${esc(ai)} only</button>`}<a class="btn sm" href="/api/audit/export.csv?ai_only=${aiOnly}">Export CSV</a></div></div>
      <div class="card audit"><div class="tbl"><table><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Detail</th><th>Outcome</th></tr></thead><tbody>
        ${d.rows.map((r) => `<tr><td class="ts">${fmtDate(r.at, { time: true })}</td><td class="who ${r.actor.startsWith('axl') ? 'ai' : ''}" title="${esc(r.actor)}">${esc(r.actor.startsWith('axl') ? ai + r.actor.slice(3) : r.actor_name || r.actor)}</td><td class="act" title="${esc(r.action)}">${esc(r.what || r.action)} ${r.target ? `<em>${esc(r.target)}</em>` : ''}</td><td class="act">${esc(r.detail)}</td><td>${pill(r.outcome, { ok: 'good', blocked: 'crit', failed: 'crit', pending: 'warn' }[r.outcome] || 'neutral')}</td></tr>`).join('') || '<tr><td colspan="5" class="empty">Nothing yet.</td></tr>'}
      </tbody></table></div><div class="bd small muted" style="border-top:1px solid var(--line)">${d.total.toLocaleString()} entries</div></div>`;
      const aiBtn = root.querySelector('#ai'); if (aiBtn) aiBtn.onclick = () => { aiOnly = !aiOnly; draw(); };
      root.querySelector('#q').onkeydown = (e) => { if (e.key === 'Enter') { q = e.target.value; draw(); } };
      root.querySelector('#action').onchange = (e) => { action = e.target.value; draw(); };
    };
    await draw();
  },
};
