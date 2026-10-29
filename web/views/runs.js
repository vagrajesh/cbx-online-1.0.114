// Approvals & flow runs. Everyone: the approvals waiting for them (approve / decline, with a note) and
// the runs they started. Owners and admins: every run of every flow — what started it, as whom it ran,
// each step it took — with Retry and Cancel. A run is opened with #/runs?id=<n> (the link in the
// "Approval needed" message).
import { api } from '../api.js';
import { esc, toast, modal, pill, fmtDate, el, aiName } from '../ui.js';
import { makeNow } from './agents-now.js';

const STATUS = { running: ['Running', 'info', true], waiting: ['Waiting', 'warn'], done: ['Done', 'good'], failed: ['Failed', 'crit'], stopped: ['Stopped', 'neutral'], cancelled: ['Cancelled', 'neutral'] };
const TRIG = { button: 'a button', row_added: 'a new row', row_changed: 'a changed row', schedule: 'its schedule', retry: 'a retry' };
const st = (s) => { const [t, k, r] = STATUS[s] || [s, 'neutral']; return pill(t, k, r); };

export default {
  title: 'Approvals',
  async render(root, ctx) {
    const admin = ctx.me.role === 'owner' || ctx.me.role === 'admin';
    let tab = 'waiting', filter = '';

    async function draw() {
      let waiting = [], runs = [], asks = 0;
      const owner = ctx.me.role === 'owner';
      try { [waiting, runs, asks] = await Promise.all([api.get('/api/flows/waiting').then((r) => r.runs), api.get('/api/flows/runs?limit=150' + (filter ? '&status=' + filter : '')).then((r) => r.runs),
        owner ? api.get('/api/cexa/now', { quiet: true }).then((r) => r.waiting.length + ((r.goals && r.goals.waiting) || []).filter((w) => w.kind === 'goal').length).catch(() => 0) : 0]); }  // 1.0.114: goals waiting for you count too
      catch (e) { root.innerHTML = `<div class="head"><div><h1>Approvals</h1><p>${esc(e.detail || e.message)}</p></div></div>`; return; }
      root.innerHTML = `
        <div class="head"><div><h1>Approvals</h1><p>${admin ? 'What flows are waiting for you, and every run of every flow — what started it, whose rights it used and each step it took.' : 'Flows that are waiting for your decision, and the ones you started.'}</p></div></div>
        <div class="tabs"><button aria-selected="${tab === 'waiting'}" data-tab="waiting">Waiting for you (${waiting.length + asks})</button><button aria-selected="${tab === 'runs'}" data-tab="runs">${admin ? 'All runs' : 'Runs I started'}</button></div>
        <div id="pane"></div>`;
      root.querySelectorAll('[data-tab]').forEach((b) => (b.onclick = () => { tab = b.dataset.tab; draw(); }));
      const pane = root.querySelector('#pane');
      if (tab === 'waiting') {
        // 1.0.83: one inbox — what C-EXA may not do alone waits here too, beside the flows
        pane.innerHTML = `<div id="cx-asks"></div><div id="flow-asks"></div>`;
        if (owner) makeNow(pane.querySelector('#cx-asks'), { canAct: true, ai: aiName(), org: () => null, part: 'asks', onCount: (n) => { if (n !== asks) { asks = n; const b = root.querySelector('[data-tab="waiting"]'); if (b) b.textContent = `Waiting for you (${waiting.length + n})`; } if (ctx.refreshCounts) ctx.refreshCounts(); } }).refresh();
        const fl = pane.querySelector('#flow-asks');
        fl.innerHTML = waiting.length ? waiting.map((r) => `<div class="card run-ask" style="margin-bottom:12px"><div class="bd">
            <div class="row between" style="gap:12px;flex-wrap:wrap"><div><div class="eyebrow">${esc(r.tool_title)} · ${esc(r.flow_title)}</div><h2 style="margin:4px 0">${esc((r.waiting || {}).question || 'May this go ahead?')}</h2><div class="small muted">Started ${fmtDate(r.started_at, { rel: true })}${r.by ? ' by ' + esc(r.by) : ''} · run #${r.id}</div></div>
            <div class="row" style="gap:8px"><button class="btn" data-decline="${r.id}">Decline</button><button class="btn primary" data-approve="${r.id}">Approve</button></div></div>
            <details class="small" style="margin-top:8px"><summary style="cursor:pointer;color:var(--accent)">What it has done so far</summary>${logHtml(r.log)}</details></div></div>`).join('')
          : (asks ? '' : '<div class="card"><div class="empty">Nothing is waiting for you.</div></div>');
        pane.querySelectorAll('[data-approve]').forEach((b) => (b.onclick = () => decide(+b.dataset.approve, true)));
        pane.querySelectorAll('[data-decline]').forEach((b) => (b.onclick = () => decide(+b.dataset.decline, false)));
      } else {
        pane.innerHTML = `<div class="row" style="gap:8px;margin-bottom:10px"><select id="f-st" style="width:auto"><option value="">Every status</option>${Object.entries(STATUS).map(([k, [t]]) => `<option value="${k}" ${filter === k ? 'selected' : ''}>${t}</option>`).join('')}</select></div>
          <div class="card"><div class="tbl"><table><thead><tr><th>Flow</th><th>Started by</th><th>Ran as</th><th>When</th><th>Steps</th><th>Status</th></tr></thead><tbody>
          ${runs.map((r) => `<tr class="link" data-open="${r.id}"><td><b>${esc(r.flow_title)}</b><div class="small muted">${esc(r.tool_title)}</div></td><td class="small">${esc(r.by || '—')}<div class="muted">${esc(TRIG[r.trigger] || r.trigger)}</div></td><td class="small">${esc(r.as || '—')}</td><td class="small">${fmtDate(r.started_at, { rel: true, time: true })}</td><td class="num">${r.steps}</td><td>${st(r.status)}${r.note && r.status === 'failed' ? `<div class="small" style="color:var(--crit);max-width:280px">${esc(r.note)}</div>` : ''}</td></tr>`).join('') || '<tr><td colspan="6" class="empty">No runs yet.</td></tr>'}
          </tbody></table></div></div>`;
        pane.querySelector('#f-st').onchange = (e) => { filter = e.target.value; draw(); };
        pane.querySelectorAll('[data-open]').forEach((tr) => (tr.onclick = () => openRun(+tr.dataset.open)));
      }
    }

    function logHtml(log) {
      if (!(log || []).length) return '<p class="small muted">No steps yet.</p>';
      return `<ol class="run-log">${log.map((x) => `<li class="${x.ok ? 'ok' : 'bad'}"><span class="mono small">${esc(x.step || '·')}</span> <b>${esc(x.type || '')}</b> ${esc(x.note || '')}<span class="small muted"> · ${fmtDate(x.at, { time: true })}</span></li>`).join('')}</ol>`;
    }

    function decide(id, approve) {
      const body = el(`<div class="field"><label>${approve ? 'Note (optional)' : 'Why not? (the flow and the person who started it see this)'}</label><textarea id="note" style="min-height:70px"></textarea></div>`);
      const m = modal({ title: approve ? 'Approve' : 'Decline', body, footer: `<button class="btn" data-close>Cancel</button><button class="btn ${approve ? 'primary' : ''}" id="ok">${approve ? 'Approve' : 'Decline'}</button>` });
      m.root.querySelector('#ok').onclick = async () => {
        try { const r = await api.post(`/api/flows/runs/${id}/decide`, { approve, note: body.querySelector('#note').value }); m.close(); toast(approve ? 'Approved — the flow carries on' : 'Declined'); if (ctx.refreshCounts) ctx.refreshCounts(); draw(); if (r.run.status === 'failed') toast('The flow stopped after your decision: ' + r.run.note, 'err'); }
        catch (e) { toast(e.detail || 'Could not record that.', 'err'); }
      };
    }

    async function openRun(id) {
      let r;
      try { r = (await api.get(`/api/flows/runs/${id}`)).run; } catch (e) { toast(e.detail || 'That run is not here.', 'err'); return; }
      const acts = [];
      if (r.can_decide) acts.push(`<button class="btn" data-d="0">Decline</button><button class="btn primary" data-d="1">Approve</button>`);
      if (admin && ['waiting', 'running'].includes(r.status)) acts.push('<button class="btn ghost" id="r-cancel">Cancel the run</button>');
      if (admin && ['failed', 'done', 'cancelled', 'stopped'].includes(r.status)) acts.push('<button class="btn" id="r-retry">Run again</button>');
      const m = modal({ title: `${r.flow_title} · run #${r.id}`, body: `<div class="row" style="gap:10px;flex-wrap:wrap;margin-bottom:10px">${st(r.status)}<span class="small muted">${esc(r.tool_title)} · started by ${esc(TRIG[r.trigger] || r.trigger)}${r.by ? ' (' + esc(r.by) + ')' : ''} · ran as ${esc(r.as || '—')} · ${fmtDate(r.started_at, { time: true })}</span></div>${r.waiting && r.waiting.question ? `<div class="thinking small" style="margin-bottom:10px">Waiting: ${esc(r.waiting.question)}</div>` : ''}${r.note ? `<p class="small">${esc(r.note)}</p>` : ''}${logHtml(r.log)}`, footer: `<button class="btn" data-close>Close</button>${acts.join('')}` });
      m.root.querySelectorAll('[data-d]').forEach((b) => (b.onclick = () => { m.close(); decide(r.id, b.dataset.d === '1'); }));
      const c = m.root.querySelector('#r-cancel'); if (c) c.onclick = async () => { try { await api.post(`/api/flows/runs/${r.id}/cancel`); m.close(); toast('Run cancelled'); draw(); } catch (e) { toast(e.detail, 'err'); } };
      const rt = m.root.querySelector('#r-retry'); if (rt) rt.onclick = async () => { try { const x = await api.post(`/api/flows/runs/${r.id}/retry`); m.close(); toast(`Started again as run #${x.run.id}`); draw(); } catch (e) { toast(e.detail, 'err'); } };
    }

    await draw();
    if (ctx.params.id) { tab = 'waiting'; openRun(+ctx.params.id); }
  },
};
