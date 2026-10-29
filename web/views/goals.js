// The owner's board (1.0.114): the goals AXL breaks down from the top — progress, who has it, what is stuck — what only
// a person can answer, and what was done today. A click opens a goal's tree: the teams' parts → the items their leads
// assigned → the requests to other teams, each with its state, who has it and what came out. Admins start a goal by
// typing it; the owner answers a goal that waits for him (Retry, Close it partly, Stop, or a direction). Users never
// see any of it (the server refuses them too).
import { api } from '../api.js';
import { esc, toast, pill, modal, el, fmtDate } from '../ui.js';

const GST = { planning: ['Planning', 'info', true], running: ['Working', 'info', true], blocked: ['Stuck — AXL is on it', 'ai'], waiting_you: ['Needs you', 'warn'],
  done: ['Done', 'good'], failed: ['Failed', 'crit'], stopped: ['Stopped', 'neutral'] };
const NST = { queued: ['Waits its turn', 'neutral'], open: ['Lined up', 'neutral'], working: ['Working', 'info', true], held: ['Waiting on others', 'ai'],
  waiting: ['Stuck', 'warn'], done: ['Done', 'good'], failed: ['Failed', 'crit'], stopped: ['Stopped', 'neutral'], declined: ['Not now', 'neutral'] };
const LEVEL = { team: 'Part', item: 'Item', request: 'Request', question: 'Question' };
const SRC = { owner: 'from the owner', admin: 'from an admin', schedule: 'a team\'s schedule', problem: 'a problem she noticed' };
const ago = (iso) => { if (!iso) return ''; const s = (Date.now() - new Date(iso).getTime()) / 1000; return s < 60 ? 'just now' : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : fmtDate(iso); };
const st = (map, s) => { const [t, k, r] = map[s] || [s, 'neutral']; return pill(t, k, r); };
const bar = (pct) => `<span class="progress" style="flex:0 0 90px;height:6px" title="${pct}%"><i style="width:${Math.max(0, Math.min(100, pct || 0))}%"></i></span>`;

export function goalRow(g) {
  const meta = [g.total ? `${g.done} of ${g.total} done` : 'being planned', g.who ? `with ${esc(g.who)}` : '', g.blocked && g.blocker ? `<span style="color:var(--warn)">${esc(g.blocker.slice(0, 110))}</span>` : ''].filter(Boolean).join(' · ');
  return `<li><a href="#" class="hb-item" data-goal="${g.id}"><div class="t"><b>${esc(g.title)}</b><span>${meta}</span></div>${g.total ? bar(g.pct) : ''}${st(GST, g.status)}</a></li>`;
}

// what the board shows in Agents → Now (`data` = /api/cexa/now's "goals")
export function boardHtml(data, { ai }) {
  if (!data) return '';
  const open = data.goals.filter((g) => !['done', 'failed', 'stopped'].includes(g.status));
  const goalWaits = data.waiting.filter((w) => w.kind === 'goal');
  const asks = data.waiting.filter((w) => w.kind === 'ask').length;
  const c = data.counts || {};
  return `
    ${data.can_start ? `<div class="card" style="margin-top:16px"><div class="bd row" style="gap:8px;flex-wrap:wrap">
      <input type="text" id="gl-new" maxlength="300" placeholder="Give ${esc(ai)} a goal — e.g. Get the Telkom profiles fixed" style="flex:1 1 280px;min-width:0">
      <button class="btn primary" id="gl-start">Start the goal</button></div></div>` : ''}
    ${goalWaits.length || asks ? `<div class="card hb-wait" style="margin-top:16px"><div class="hd"><h2>Waiting for you</h2><span class="small muted">${goalWaits.length + asks} · ${esc(ai)} tried; a person decides these</span></div>
      <div class="bd hb-asks">${goalWaits.map((w) => `<div class="hb-ask"><div class="row between" style="gap:8px"><b>${esc(w.title)}</b><a href="#" class="small" data-goal="${w.id}">open</a></div>
        <div class="hb-q small">${esc(w.text)}</div>${w.recommend ? `<div class="small"><b>${esc(ai)} recommends:</b> ${esc(w.recommend)}</div>` : ''}
        ${data.can_act ? `<div class="row" style="gap:6px;flex-wrap:wrap;margin-top:6px">${w.options.map((o) => `<button class="btn sm ${/^stop/i.test(o) ? 'ghost' : ''}" data-ganswer="${w.id}" data-text="${esc(o)}">${esc(o)}</button>`).join('')}</div>` : ''}</div>`).join('')}
        ${asks ? `<div class="small">${asks} question${asks === 1 ? '' : 's'} and tap${asks === 1 ? '' : 's'} only you may approve — <a href="#/runs">open Approvals</a></div>` : ''}</div></div>` : ''}
    <div class="grid g2 hb-grid" style="gap:16px;align-items:start;margin-top:16px">
      <div class="card"><div class="hd"><h2>Goals</h2><span class="small muted">${c.running || 0} working${c.blocked ? ` · ${c.blocked} stuck` : ''}</span></div>
        <ul class="list hb-list">${open.map(goalRow).join('') || `<li class="empty">No goals in progress. ${esc(ai)} turns what she notices into goals; ${data.can_start ? 'you can give her one above.' : 'an admin can give her one.'}</li>`}</ul></div>
      <div class="card"><div class="hd"><h2>Done today</h2><span class="small muted">${c.done_today || 0}</span></div>
        <ul class="list hb-list">${data.done_today.map(goalRow).join('') || '<li class="empty">Nothing yet today.</li>'}</ul></div>
    </div>`;
}

// buttons and links the board (and Approvals) render; `after` reloads the page that shows them
export function wireGoals(box, { after, ai, canAct = false }) {
  box.querySelectorAll('[data-goal]').forEach((a) => (a.onclick = (e) => { e.preventDefault(); openGoal(+a.dataset.goal, { after, ai, canAct }); }));
  box.querySelectorAll('[data-ganswer]').forEach((b) => (b.onclick = () => answerGoal(+b.dataset.ganswer, b.dataset.text, b, after)));
  const go = box.querySelector('#gl-start'), inp = box.querySelector('#gl-new');
  if (go && inp) {
    const start = async () => {
      const title = inp.value.trim(); if (!title) { inp.focus(); return; }
      go.disabled = true;
      try { const g = await api.post('/api/cexa/goals', { title }); inp.value = ''; toast(`${ai} has it — she plans it now`); await after(); openGoal(g.id, { after, ai, canAct }); }
      catch (e) { toast(e.detail || 'Could not start it', 'err'); } finally { go.disabled = false; }
    };
    go.onclick = start; inp.onkeydown = (e) => { if (e.key === 'Enter') start(); };
  }
}

async function answerGoal(id, text, btn, after) {
  if (btn) btn.disabled = true;
  try { await api.post(`/api/cexa/goals/${id}/answer`, { text: /^close/i.test(text) ? 'close' : text }); toast('Thanks — passed on'); await after(); }
  catch (e) { toast(e.detail || 'Could not answer', 'err'); if (btn) btn.disabled = false; }
}

function nodeHtml(n) {
  const kids = (n.children || []).map(nodeHtml).join('');
  return `<li style="padding:6px 0${n.status === 'stopped' ? ';opacity:.6' : ''}"><div class="row between" style="gap:8px;align-items:flex-start"><div style="min-width:0">
      <span class="small muted">${esc(LEVEL[n.level] || 'Work')} · ${esc(n.team)} · ${esc(n.who)}${n.attempts ? ` · try ${n.attempts + 1}` : ''}</span>
      <div><b>${esc(n.title)}</b></div>
      ${n.done_when ? `<div class="small muted">Done when: ${esc(n.done_when)}</div>` : ''}
      ${n.blocker ? `<div class="small" style="color:var(--warn)">${esc(n.blocker)}</div>` : ''}
      ${n.result && ['done', 'failed'].includes(n.status) ? `<div class="small">${esc(n.result.slice(0, 400))}</div>` : ''}</div>${st(NST, n.status)}</div>
    ${kids ? `<ul class="list" style="margin:6px 0 0 14px;padding-left:10px;border-left:2px solid var(--line)">${kids}</ul>` : ''}</li>`;
}

export async function openGoal(id, { after, ai, canAct = false }) {
  let g;
  try { g = await api.get(`/api/cexa/goals/${id}`); } catch (e) { toast(e.detail || 'Could not open it', 'err'); return; }
  const live = ['planning', 'running', 'blocked', 'waiting_you'].includes(g.status);
  const body = el(`<div>
    <div class="row between" style="gap:8px;flex-wrap:wrap"><span class="small muted">${esc(SRC[g.source] || g.source)}${g.created_by ? ' · ' + esc(g.created_by) : ''} · #${g.id} · ${esc(ago(g.created_at))}</span>${st(GST, g.status)}</div>
    ${g.why ? `<div class="ag-brief small" style="margin-top:8px">${esc(g.why)}</div>` : ''}
    ${g.done_when ? `<p class="small"><b>Done when:</b> ${esc(g.done_when)}</p>` : ''}
    <div class="row" style="gap:10px;margin-top:10px">${bar(g.pct)}<span class="small">${g.done} of ${g.total} done${g.who ? ` · ${esc(g.who)} has it now` : ''} · ${g.calls_used} of ${g.budget_calls} AI calls</span></div>
    ${g.recommend ? `<div class="hb-ask" style="margin-top:12px"><div class="hb-q">${esc(g.recommend)}</div>${canAct ? `<div class="row" style="gap:6px;flex-wrap:wrap;margin-top:6px">
      <button class="btn sm" data-ganswer="${g.id}" data-text="Retry">Retry</button><button class="btn sm" data-ganswer="${g.id}" data-text="close">Close it partly</button>
      <input type="text" id="gl-say" placeholder="Or tell ${esc(ai)} what to do differently…" maxlength="500" style="flex:1 1 200px"><button class="btn sm" id="gl-say-go">Send</button></div>` : ''}</div>` : ''}
    ${g.summary ? `<div class="ag-summary" style="margin-top:10px">${esc(g.summary)}</div>` : ''}
    <div class="eyebrow" style="margin-top:14px">How it is broken down</div>
    <ul class="list" style="margin:4px 0 0">${(g.tree || []).map(nodeHtml).join('') || '<li class="empty">Being planned…</li>'}</ul>
    ${(g.events || []).length ? `<details style="margin-top:12px"><summary class="small muted" style="cursor:pointer">What ${esc(ai)} decided (${g.events.length})</summary><ol class="small">${g.events.map((e) => `<li>${esc(ago(e.at))} — ${esc(e.text)}</li>`).join('')}</ol></details>` : ''}
  </div>`);
  const d = modal({ title: g.title, body, footer: `${live ? '<button class="btn ghost danger" id="gl-stop">Stop the goal</button>' : ''}<button class="btn" data-close>Close</button>` });
  const done = async () => { d.close(); await after(); };
  body.querySelectorAll('[data-ganswer]').forEach((b) => (b.onclick = async () => { await answerGoal(id, b.dataset.text, b, async () => {}); await done(); }));
  const say = body.querySelector('#gl-say-go');
  if (say) say.onclick = async () => { const v = body.querySelector('#gl-say').value.trim(); if (!v) return; await answerGoal(id, v, say, async () => {}); await done(); };
  const s = d.root.querySelector('#gl-stop');
  if (s) s.onclick = async () => { try { await api.post(`/api/cexa/goals/${id}/stop`, {}); toast('Stopped'); await done(); } catch (e) { toast(e.detail || 'Could not stop it', 'err'); } };
}

// a link like #/agents?tab=now&goal=12 (the "Goal waits for you" message) opens that goal once
export function openFromHash({ after, ai, canAct = false }) {
  const m = /[?&]goal=(\d+)/.exec(location.hash || '');
  if (!m) return;
  history.replaceState(null, '', location.hash.replace(/[?&]goal=\d+/, ''));
  openGoal(+m[1], { after, ai, canAct });
}
